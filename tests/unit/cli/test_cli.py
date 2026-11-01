from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from emporio.cli import main
from emporio.errors import ProviderFatal, ProviderUnavailable
from support.fakes import FakeLLM, say, use

LUCAS = "lucas.mendes@jmail.com"  # pedido 4

Run = Callable[..., int]


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    return {"db": tmp_path / "sessions.db", "log": tmp_path / "turns.jsonl", "dir": tmp_path}


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, paths: dict[str, Path]) -> Run:
    """Roda ``main`` com um modelo roteirizado e as linhas digitadas (exceção = Ctrl-C ou Ctrl-D)."""

    def _run(
        args: list[str],
        lines: list[Any] | None = None,
        llm: FakeLLM | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        typed = iter(lines or [])

        def fake_input(prompt: str = "") -> str:
            item = next(typed, EOFError())
            if isinstance(item, BaseException):
                raise item
            return str(item)

        monkeypatch.setattr("builtins.input", fake_input)
        base = {
            "OPENAI_API_KEY": "chave-de-teste",
            "LLM_MODEL": "modelo-x",
            "EMPORIO_HISTORY_DB": str(paths["db"]),
        }
        model = llm or FakeLLM()
        return main(args, env=env if env is not None else base, llm_factory=lambda m, e: model)

    return _run


def stored(path: Path, session_id: str | None = None) -> int:
    with sqlite3.connect(path) as db:
        query, params = "SELECT COUNT(*) FROM messages", ()
        if session_id:
            query, params = query + " WHERE session_id = ?", (session_id,)
        return int(db.execute(query, params).fetchone()[0])


# --- banner e configuração --------------------------------------------------------------------


def test_banner_is_short_and_lists_the_commands(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["--now", "2026-02-20T10:00"], ["/sair"]) == 0
    err = capsys.readouterr().err
    assert "/ajuda  /novo  /esquecer  /trace  /sair" in err
    assert "Relógio de demonstração: sexta-feira, 20/02/2026 10:00" in err  # relógio congelado
    assert "modelo" not in err  # os detalhes da execução só aparecem com --trace


def test_banner_does_not_mention_the_clock_when_it_is_the_real_one(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run([], ["/sair"]) == 0
    assert "Relógio" not in capsys.readouterr().err


def test_trace_banner_declares_model_and_clock(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["--trace", "--now", "2026-02-20T10:00"], ["/sair"]) == 0
    err = capsys.readouterr().err
    assert "modelo fake-model (OpenAI)" in err
    assert "sexta-feira, 20/02/2026 10:00" in err and "congelado por --now" in err


def test_help_says_what_is_sent_to_the_provider(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    run([], ["/ajuda", "/sair"])
    out = capsys.readouterr().out
    assert "enviadas à OpenAI" in out and "CPF" in out


def test_missing_model_and_key_exit_with_guidance(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([], env={"OPENAI_API_KEY": "k"}) == 3
    assert "LLM_MODEL" in capsys.readouterr().err
    assert main([], env={"LLM_MODEL": "m"}) == 3
    err = capsys.readouterr().err
    assert "OPENAI_API_KEY" in err and "--demo" in err and "Traceback" not in err


def test_invalid_clock_exits_with_config_error(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["--now", "ontem"]) == 3
    assert "--now inválido" in capsys.readouterr().err


def test_clock_can_come_from_the_environment(
    run: Run, capsys: pytest.CaptureFixture[str], paths: dict[str, Path]
) -> None:
    env = {
        "OPENAI_API_KEY": "k",
        "LLM_MODEL": "m",
        "AGENT_NOW": "2026-03-23T10:00",
        "EMPORIO_HISTORY_DB": str(paths["db"]),
    }
    assert run(["--trace"], ["/sair"], env=env) == 0
    assert "segunda-feira, 23/03/2026 10:00" in capsys.readouterr().err


def test_check_data(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check-data"], env={}) == 0
    assert "dados ok" in capsys.readouterr().out
    assert main(["--check-data", "--data", "/nao/existe"], env={}) == 3
    assert "arquivos ausentes" in capsys.readouterr().err


# --- conversa e trace --------------------------------------------------------------------------


def test_conversation_with_trace_shows_tools_tokens_and_latency(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = FakeLLM(use("get_product", name="gd20"), say("O GD20 sai por R$ 2.199,00."))
    assert run(["--trace"], ["Quanto custa o GD20?", "/sair"], llm) == 0
    out, err = capsys.readouterr()
    assert "Você> Quanto custa o GD20?" in out and "Téo> O GD20 sai por R$ 2.199,00." in out
    assert "[ferramenta] get_product ok" in err and 'argumentos: {"name": "gd20"}' in err
    assert "resultado :" in err and "2.199,00" in err
    assert "1 consulta(s) | 2 chamada(s) ao modelo" in err
    assert "tokens entrada/saída/cache: 20/10/0" in err and "trace_id=" in err


def test_trace_never_prints_the_proof(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    llm = FakeLLM(use("verify_customer", order_id=4, proof=LUCAS), say("Confirmei."))
    run(["--trace"], ["pedido 4, " + LUCAS, "/sair"], llm)
    err = capsys.readouterr().err
    assert '"proof": "***"' in err and "jmail" not in err.split("[ferramenta]")[1]


def test_without_trace_no_tools_are_shown(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    run([], ["oi", "/sair"], FakeLLM(say("Oi!")))
    assert "[ferramenta]" not in capsys.readouterr().err


def test_guard_actions_show_up_in_the_trace(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    llm = FakeLLM(say("Sai por R$ 1,23."), say("Sai por R$ 4,56."))
    run(["--trace"], ["quanto custa?", "/sair"], llm)
    err = capsys.readouterr().err
    assert "guarda de fundamentação barrou" in err and "resposta segura no lugar" in err


def test_one_shot_messages(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    llm = FakeLLM(say("Oi!"), say("Tudo bem?"))
    assert run(["-m", "oi", "-m", "tudo bem?"], None, llm) == 0
    out = capsys.readouterr().out
    assert out.index("Você> oi") < out.index("Téo> Oi!") < out.index("Você> tudo bem?")


# --- teclado -----------------------------------------------------------------------------------


def test_ctrl_d_exits_cleanly(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    assert run([], [EOFError()]) == 0
    assert "Até logo!" in capsys.readouterr().err


def test_ctrl_c_once_warns_and_twice_exits(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    code = run(
        [],
        [KeyboardInterrupt(), "oi", KeyboardInterrupt(), KeyboardInterrupt()],
        FakeLLM(say("Oi!")),
    )
    cap = capsys.readouterr()
    assert code == 130 and cap.err.count("Pressione de novo") == 2 and "Téo> Oi!" in cap.out


def test_ctrl_c_during_a_turn_cancels_it_without_saving(
    run: Run, paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    llm = FakeLLM(KeyboardInterrupt())
    assert run([], ["oi", "/sair"], llm) == 0
    assert "Turno cancelado" in capsys.readouterr().err and stored(paths["db"]) == 0


# --- comandos e entrada ------------------------------------------------------------------------


def test_commands(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    assert run([], ["/ajuda", "/trace", "/trace", "/foo", "/sair"]) == 0
    cap = capsys.readouterr()
    assert "/esquecer" in cap.out
    assert "Trace: ligado." in cap.err and "Trace: desligado." in cap.err
    assert "Comando desconhecido: /foo. Digite /ajuda." in cap.err


def test_long_input_is_refused_and_control_characters_are_removed(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = FakeLLM(say("Oi!"))
    run([], ["a" * 1001, "oi\x00‮", "/sair"], llm)
    cap = capsys.readouterr()
    assert "Mensagem longa demais" in cap.err and "Você> oi\n" in cap.out
    assert llm.calls[0].messages[-1].content == "oi"


def test_forget_clears_the_history_and_the_verification(
    run: Run, paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    llm = FakeLLM(
        use("verify_customer", order_id=4, proof=LUCAS),
        say("Confirmei, Lucas."),
        use("get_order_status", order_id=4),
        say("Preciso confirmar seus dados de novo."),
    )
    run(
        ["--trace", "--session", "s1"],
        ["pedido 4, " + LUCAS, "/esquecer", "e o status?", "/sair"],
        llm,
    )
    err = capsys.readouterr().err
    assert "apaguei o histórico" in err
    assert "[ferramenta] get_order_status erro" in err  # a verificação se foi com o /esquecer
    assert stored(paths["db"], "s1") == 2  # só a troca depois do /esquecer


def test_forget_leaves_nothing_in_the_database(run: Run, paths: dict[str, Path]) -> None:
    run(["--session", "s1"], ["oi", "/esquecer", "/sair"], FakeLLM(say("Oi!")))
    assert stored(paths["db"], "s1") == 0


def test_new_conversation_keeps_the_previous_one_resumable(
    run: Run, paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        ["--session", "s1"],
        ["oi", "/novo", "tudo bem?", "/sair"],
        FakeLLM(say("Oi!"), say("Tudo!")),
    )
    assert "--session s1" in capsys.readouterr().err
    assert stored(paths["db"], "s1") == 2 and stored(paths["db"]) == 4

    llm = FakeLLM(say("Continuando."))
    run(["--trace", "--session", "s1"], ["e aí?", "/sair"], llm)
    assert "retomada, 2 mensagens" in capsys.readouterr().err
    assert [m.content for m in llm.calls[0].messages[1:]] == ["oi", "Oi!", "e aí?"]


# --- falhas do provedor ------------------------------------------------------------------------


def test_rate_limit_is_reported_politely_and_the_turn_is_not_saved(
    run: Run, paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    llm = FakeLLM(ProviderUnavailable("429", rate_limited=True), say("Agora foi."))
    assert run(["--session", "s1"], ["oi", "oi de novo", "/sair"], llm) == 0  # a conversa continua
    cap = capsys.readouterr()
    assert "muita demanda" in cap.out and "R$" not in cap.out.split("Agora foi")[0]
    assert "Turno não gravado" in cap.err and "Traceback" not in cap.err
    assert stored(paths["db"], "s1") == 2  # só a troca bem-sucedida


def test_one_shot_exit_code_reflects_the_failure(run: Run) -> None:
    assert run(["-m", "oi"], None, FakeLLM(ProviderUnavailable("502"))) == 4
    assert run(["-m", "oi"], None, FakeLLM(RuntimeError("bug"))) == 1


def test_bad_credentials_stop_the_session_and_hide_the_key(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = FakeLLM(ProviderFatal("Incorrect API key sk-abcdefghijklmnopqrstuvwxyz1234"))
    assert run([], ["oi", "nunca lido"], llm) == 3
    err = capsys.readouterr().err
    assert "OPENAI_API_KEY" in err and "sk-abcdefghijklmnop" not in err and "***" in err


def test_unexpected_error_hides_the_stack_unless_debug(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    run([], ["oi", "/sair"], FakeLLM(RuntimeError("bug interno")))
    cap = capsys.readouterr()
    assert "Algo deu errado do meu lado" in cap.out and "Traceback" not in cap.err
    run(["--debug"], ["oi", "/sair"], FakeLLM(RuntimeError("bug interno")))
    assert "Traceback" in capsys.readouterr().err


def test_unknown_command_suggests_the_closest_one(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    run([], ["/ajda", "/sair"])
    assert "Quis dizer /ajuda?" in capsys.readouterr().err


def test_leaving_a_conversation_with_history_says_how_to_resume_it(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    run(["--session", "minha"], ["oi", "/sair"], FakeLLM(say("Oi!")))
    assert "emporio --session minha" in capsys.readouterr().err
    run([], ["/sair"])
    assert "--session" not in capsys.readouterr().err  # conversa vazia: nada a retomar


def test_one_shot_banner_has_no_commands(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    run(["-m", "oi"], None, FakeLLM(say("Oi!")))
    assert "/ajuda" not in capsys.readouterr().err
