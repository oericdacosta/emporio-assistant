"""Grava as conversas de exemplo contra o modelo real e escreve ``examples/conversations/``.

Uso:  uv run python scripts/record_examples.py [--model <modelo>]
A chave vem de OPENAI_API_KEY (ou do .env). Para cada cenário grava um ``.json`` (lido pela aba
Exemplos do Streamlit) e um ``.md`` (o entregável legível). Tools, dados e regras são os de verdade.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from emporio import envfile
from emporio.agent.prompt import prompt_version
from emporio.agent.session import Session
from emporio.app import build_agent, load_data
from emporio.clock import FixedClock
from emporio.llm.factory import make_llm
from emporio.paths import default_data_dir

OUT = Path(__file__).resolve().parents[1] / "examples" / "conversations"
DEFAULT_NOW = "2026-03-23T10:00:00-04:00"  # segunda-feira, loja aberta

SCENARIOS: list[dict[str, Any]] = [
    {
        "slug": "01-violoes-ate-1000",
        "title": "Busca no catálogo com filtro de preço",
        "kind": "Catálogo",
        "note": "Consulta o catálogo em tempo real; só mostra o que está à venda.",
        "now": DEFAULT_NOW,
        "turns": ["Quais opções de violões vocês têm custando até R$1000?"],
    },
    {
        "slug": "02-preco-e-parcelamento",
        "title": "Preço de um produto e condições de pagamento",
        "kind": "Preço e pagamento",
        "note": "O preço vem do catálogo; as parcelas e o Pix são calculados em código, não pelo modelo.",
        "now": DEFAULT_NOW,
        "turns": [
            "Quanto custa o Takamine GD20?",
            "Dá pra parcelar em 10x? E no Pix tem desconto?",
        ],
    },
    {
        "slug": "03-loja-fechada-e-fora-de-escopo",
        "title": "Informações da loja (domingo) e pedido fora do escopo",
        "kind": "Loja e escopo",
        "note": "O relógio simulado é um domingo: o agente diz que a loja está fechada e quando abre. Depois recusa o que não é da loja.",
        "now": "2026-03-22T10:00:00-04:00",
        "turns": [
            "Vocês estão abertos agora? E qual o endereço?",
            "Aproveitando, me passa uma receita de bolo?",
        ],
    },
    {
        "slug": "04-devolucao-fora-do-prazo",
        "title": "Devolução por arrependimento (aplica a política)",
        "kind": "Política e pedido (não trivial)",
        "note": "Pede a prova de identidade, verifica o pedido e confere o prazo de 7 dias contra a data de recebimento.",
        "now": DEFAULT_NOW,
        "turns": [
            "Me arrependi da minha compra, posso devolver meu pedido?",
            "É o pedido 7, recebi dia 17/02. Meu e-mail é leticia.rocha@jmail.com",
        ],
    },
    {
        "slug": "05-status-de-pedido-com-verificacao",
        "title": "Status de pedido com verificação de identidade",
        "kind": "Pedido e segurança (não trivial)",
        "note": "O agente pede o número do pedido e uma prova de identidade. Com o e-mail errado, não revela nada nem confirma que o pedido existe; depois que o cliente corrige, informa status e rastreio.",
        "now": DEFAULT_NOW,
        "turns": [
            "Oi, queria saber onde está meu pedido.",
            "É o pedido 8, meu e-mail é lucas.mendes@jmail.com",
            "Ops, errei o e-mail. O certo é anacarol.ferreira@coldmail.com",
        ],
    },
]


def _commit() -> str:
    try:
        done = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def record(scenario: dict[str, Any], llm: Any, repo: Any, policies: Any) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as scratch:
        agent = build_agent(
            llm,
            repo=repo,
            clock=FixedClock.parse(scenario["now"]),
            policies=policies,
            complaints_log=Path(scratch) / "complaints.jsonl",
        )
        session = Session(f"example-{scenario['slug']}")
        for text in scenario["turns"]:
            result = agent.run_turn(session, text)
            turns.append(
                {
                    "user": text,
                    "assistant": result.text,
                    "tools": [
                        {"name": t.name, "arguments": t.arguments, "ok": t.ok} for t in result.tools
                    ],
                    "guard_blocked": result.guard_blocked,
                    "fallback": result.fallback,
                }
            )
    return turns


def to_markdown(item: dict[str, Any]) -> str:
    lines = [
        f"# {item['title']}",
        "",
        f"- **Tipo:** {item['kind']}",
        f"- **Relógio simulado:** {item['now']}",
        f"- **Modelo:** {item['model']} (gravado em {item['recorded_on']})",
        "",
        item["note"],
        "",
        "---",
        "",
    ]
    for turn in item["turns"]:
        lines += [f"**Cliente:** {turn['user']}", "", f"**Téo:** {turn['assistant']}", ""]
        if turn["tools"]:
            used = ", ".join(
                f"`{t['name']}({json.dumps(t['arguments'], ensure_ascii=False)})`"
                for t in turn["tools"]
            )
            lines += [f"> Consultas feitas: {used}", ""]
        else:
            lines += ["> Nenhuma consulta: respondido sem usar tools.", ""]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None)
    parser.add_argument("--only", default=None, help="grava só o cenário cujo slug começa assim")
    args = parser.parse_args()
    envfile.load(Path(".env"))
    llm = make_llm(args.model, os.environ)
    repo, policies = load_data(default_data_dir())
    OUT.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        if args.only and not scenario["slug"].startswith(args.only):
            continue
        item = {
            **{k: scenario[k] for k in ("slug", "title", "kind", "note", "now")},
            "model": args.model or os.environ.get("LLM_MODEL", ""),
            "recorded_on": date.today().isoformat(),
            "commit": _commit(),
            "prompt_version": prompt_version(),
            "turns": record(scenario, llm, repo, policies),
        }
        (OUT / f"{scenario['slug']}.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (OUT / f"{scenario['slug']}.md").write_text(to_markdown(item), encoding="utf-8")
        print(f"gravado {scenario['slug']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
