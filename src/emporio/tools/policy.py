"""Tool de política: o texto literal do manual, por tópico."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from emporio.data.policies import MAX_TOPICS, TOPIC_HELP, Topic
from emporio.tools.deps import ToolContext
from emporio.tools.registry import Params, Tool


class SearchPolicyParams(Params):
    topics: list[Topic] = Field(min_length=1, max_length=MAX_TOPICS)
    question: str | None = Field(default=None, max_length=500)


def search_policy_tool(ctx: ToolContext, args: SearchPolicyParams) -> dict[str, Any]:
    catalog = [*(p.name for p in ctx.repo.products()), *(c.name for c in ctx.repo.categories())]
    coverage = ctx.policies.coverage(args.question or ctx.user_text, catalog)
    result: dict[str, Any] = {
        "sections": [
            {
                "number": section.number,
                "title": section.title,
                "topic": section.topic.value,
                "text": section.text,
                "notes": list(section.notes),
            }
            for section in ctx.policies.get(args.topics)
        ],
        "coverage": coverage.level,
        "unmatched_terms": list(coverage.unmatched),
    }
    unmatched = ", ".join(coverage.unmatched)
    if coverage.level == "partial":
        result["note"] = f"Responda só com o que o manual diz; não afirme nada sobre: {unmatched}."
    elif coverage.level == "none":
        result["note"] = (
            f"O manual não trata de: {unmatched}. Não afirme nada sobre isso; diga que vai "
            "confirmar com a equipe e use register_complaint."
        )
    return result


def tools() -> list[Tool[Any]]:
    topics = "; ".join(f"{topic.value} ({words})" for topic, words in TOPIC_HELP.items())
    return [
        Tool(
            "search_policy",
            "Texto oficial do manual de políticas da loja, por tópico (até "
            f"{MAX_TOPICS}; 'todos' devolve o manual inteiro). USE para trocas e devoluções, "
            "garantia, pagamento, frete, promoções, privacidade e regras de atendimento. Cite "
            "só o que vier no texto e nas `notes`, que valem sobre o texto quando divergem. "
            "Passe em `question` a pergunta do cliente: a resposta traz `coverage` "
            "(covered/partial/none) e `unmatched_terms`, o que o manual não trata. Se não "
            "cobrir, diga que vai confirmar com a equipe. "
            f"Tópicos: {topics}.",
            SearchPolicyParams,
            search_policy_tool,
        ),
    ]
