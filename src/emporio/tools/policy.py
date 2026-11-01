"""Tool de política: o texto literal do manual, por tópico."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from emporio.data.policies import MAX_TOPICS, TOPIC_HELP, Topic
from emporio.tools.deps import ToolContext
from emporio.tools.registry import Params, Tool


class SearchPolicyParams(Params):
    topics: list[Topic] = Field(min_length=1, max_length=MAX_TOPICS)


def search_policy_tool(ctx: ToolContext, args: SearchPolicyParams) -> dict[str, Any]:
    return {
        "sections": [
            {
                "number": section.number,
                "title": section.title,
                "topic": section.topic.value,
                "text": section.text,
                "notes": list(section.notes),
            }
            for section in ctx.policies.get(args.topics)
        ]
    }


def tools() -> list[Tool[Any]]:
    topics = "; ".join(f"{topic.value} ({words})" for topic, words in TOPIC_HELP.items())
    return [
        Tool(
            "search_policy",
            "Texto oficial do manual de políticas da loja, por tópico (até "
            f"{MAX_TOPICS}; 'todos' devolve o manual inteiro). USE para trocas e devoluções, "
            "garantia, pagamento, frete, promoções, privacidade e regras de atendimento. Cite "
            "só o que vier no texto e nas `notes`, que valem sobre o texto quando divergem. "
            "Se o manual não cobrir a pergunta, diga que vai consultar a gerência. "
            f"Tópicos: {topics}.",
            SearchPolicyParams,
            search_policy_tool,
        ),
    ]
