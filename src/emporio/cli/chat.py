"""A conversa: um turno, os comandos de sessão, o REPL e o modo de mensagens avulsas."""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable, Sequence
from typing import Any

from emporio.agent import turn_log
from emporio.agent.failures import describe
from emporio.agent.graph import Agent
from emporio.agent.session import Session, SQLiteSessionStore, new_session_id
from emporio.agent.types import TurnResult
from emporio.cli import codes
from emporio.cli.text import HELP, MAX_CHARS, UNEXPECTED_MESSAGE, sanitize, scrub
from emporio.cli.ui import Ui
from emporio.clock import Clock
from emporio.errors import CassetteError, LLMError

NOT_SAVED = "Turno não gravado."


class Chat:
    def __init__(
        self,
        agent: Agent[Any],
        session: Session,
        ui: Ui,
        *,
        clock: Clock,
        store: SQLiteSessionStore | None = None,
        log_turns: bool = False,
        read_line: Callable[[str], str] | None = None,
    ) -> None:
        self.agent = agent
        self.session = session
        self.ui = ui
        self._clock = clock
        self._store = store  # None na demo: nada é gravado
        self._log_turns = log_turns
        self._read_line = read_line or input

    # --- um turno ------------------------------------------------------------------------------

    def turn(self, text: str) -> int:
        """Um turno. Toda falha vira mensagem amigável e código de saída; o stack trace só aparece
        com ``--debug``. Só um turno bem-sucedido é gravado."""
        ui = self.ui
        ui.busy("Téo está digitando...")
        try:
            result = self.agent.run_turn(self.session, text, ui.on_event)
            if self._store:
                self._store.save(self.session, result.new_messages)
        except KeyboardInterrupt:
            ui.idle()
            ui.note("Turno cancelado. Nada foi salvo nesta troca; pode reenviar.")
            return codes.INTERRUPTED
        except CassetteError as error:
            ui.idle()
            ui.error(str(error))
            return codes.CASSETTE
        except LLMError as error:
            ui.idle()
            return self._provider_failure(error)
        except Exception as error:
            ui.idle()
            ui.reply(UNEXPECTED_MESSAGE)
            ui.note(
                f"  [erro] {type(error).__name__} (rode com --debug para os detalhes). {NOT_SAVED}",
                "33",
            )
            if ui.debug:
                traceback.print_exc()
            return codes.UNEXPECTED
        ui.idle()
        self._record(result)
        ui.reply(result.text)
        if ui.trace:
            ui.turn_summary(result)
        return codes.OK

    def _provider_failure(self, error: LLMError) -> int:
        failure = describe(error)
        if self._log_turns:
            turn_log.write(turn_log.failure_record(self.session.id, error, self._clock.now()))
        if failure.misconfigured:
            self.ui.error(
                f"a OpenAI recusou a requisição ({scrub(str(error))}). "
                "Confira OPENAI_API_KEY e o modelo (--model ou LLM_MODEL)."
            )
            return codes.CONFIG
        self.ui.reply(failure.message)
        self.ui.note(f"  [provedor] {type(error).__name__}: {scrub(str(error))}. {NOT_SAVED}", "33")
        return codes.UNAVAILABLE

    def _record(self, result: TurnResult) -> None:
        if self._log_turns:
            turn_log.write(turn_log.turn_record(self.session.id, result, self._clock.now()))

    # --- comandos de sessão --------------------------------------------------------------------

    def command(self, line: str) -> bool:
        """Executa um comando. Devolve False quando é para encerrar."""
        name = line.split()[0]
        ui = self.ui
        if name in ("/sair", "/exit", "/quit"):
            return False
        if name == "/ajuda":
            ui.say(HELP)
        elif name == "/novo":
            previous, self.session = self.session.id, Session(new_session_id())
            ui.note(
                f"Nova conversa ({self.session.id}). A anterior continua salva: --session {previous}"
            )
        elif name == "/esquecer":
            if self._store:
                self._store.delete(self.session.id)
            self.session = Session(self.session.id)  # zera também a verificação de identidade
            ui.note("Pronto: apaguei o histórico desta conversa e a verificação de identidade.")
        elif name == "/trace":
            ui.trace = not ui.trace
            ui.note(f"Trace: {'ligado' if ui.trace else 'desligado'}.")
        else:
            ui.note(f"Comando desconhecido: {name}. Digite /ajuda.")
        return True

    # --- laços ---------------------------------------------------------------------------------

    def repl(self) -> int:
        ui = self.ui
        interactive = sys.stdin.isatty()
        interrupts = 0
        while True:
            try:
                line = self._read_line(ui.prompt() if interactive else "")
            except EOFError:  # Ctrl-D
                ui.say()
                ui.note("Até logo!")
                return codes.OK
            except KeyboardInterrupt:  # Ctrl-C no prompt: uma vez avisa, duas seguidas sai
                interrupts += 1
                if interrupts >= 2:
                    ui.say()
                    return codes.INTERRUPTED
                ui.note("\n(Ctrl-C) Pressione de novo para sair, ou use /sair.")
                continue
            interrupts = 0
            line = sanitize(line)
            if not line:
                continue
            if not interactive:
                ui.echo(line)
            if line.startswith("/"):
                if not self.command(line):
                    ui.note("Até logo!")
                    return codes.OK
            elif len(line) > MAX_CHARS:
                ui.note(
                    f"Mensagem longa demais ({len(line)} caracteres; máx. {MAX_CHARS}). Resuma, por favor."
                )
            elif self.turn(line) == codes.CONFIG:  # chave ou modelo errado: insistir não adianta
                return codes.CONFIG

    def play(self, texts: Sequence[str]) -> int:
        """Envia as mensagens em ordem e para na primeira falha."""
        for text in texts:
            text = sanitize(text)
            self.ui.echo(text)
            if len(text) > MAX_CHARS:
                self.ui.error(f"mensagem longa demais ({len(text)} caracteres; máx. {MAX_CHARS}).")
                return codes.USAGE
            if code := self.turn(text):
                return code
        return codes.OK
