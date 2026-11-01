from __future__ import annotations

from typing import Any

from evals.cases import Case, Hard, ToolExpectation, parse_case
from evals.checks import Conversation, check, norm
from evals.facts import Facts

from emporio.agent.loop import ToolRecord


def tool(
    tool_name: str, /, ok: bool = True, error: str | None = None, **arguments: Any
) -> ToolRecord:
    return ToolRecord(tool_name, arguments, ok, error, 0.01, 10)


def case(**hard: Any) -> Case:
    return Case("c", "i", ("pergunta",), hard=Hard(**hard))


def say(*answers: str, tools: tuple[ToolRecord, ...] = (), **flags: Any) -> Conversation:
    return Conversation(tuple(answers), tools, **flags)


def test_norm_strips_accents_case_and_non_breaking_spaces() -> None:
    assert norm("Preço\xa0R$ 2.199,00 Violão") == "preco r$ 2.199,00 violao"


def test_a_clean_answer_passes() -> None:
    verdict = check(case(contains=(("oi",),)), say("Oi, tudo bem?"))
    assert verdict.passed and verdict.soft == []


def test_required_tools_with_arguments() -> None:
    wanted = ToolExpectation("search_products", {"max_price_brl": 1000})
    hard = case(tools_must=(wanted,))
    assert check(hard, say("ok", tools=(tool("search_products", max_price_brl=1000.0),))).passed
    assert not check(hard, say("ok", tools=(tool("search_products", max_price_brl=900),))).passed
    assert not check(hard, say("ok", tools=(tool("get_product"),))).passed


def test_arguments_match_by_text_number_and_list() -> None:
    by_text = case(tools_must=(ToolExpectation("get_product", {"name": "gd20"}),))
    assert check(by_text, say("ok", tools=(tool("get_product", name="Takamine GD20"),))).passed
    by_list = case(tools_must=(ToolExpectation("search_policy", {"topics": ["garantia"]}),))
    assert check(
        by_list, say("ok", tools=(tool("search_policy", topics=["garantia", "pagamento"]),))
    ).passed
    assert not check(
        by_list, say("ok", tools=(tool("search_policy", topics=["pagamento"]),))
    ).passed
    by_accent = case(tools_must=(ToolExpectation("estimate_shipping", {"city": "Dourados"}),))
    assert check(by_accent, say("ok", tools=(tool("estimate_shipping", city="DOURADOS"),))).passed


def test_any_of_forbidden_and_none() -> None:
    options = (ToolExpectation("get_product"), ToolExpectation("search_products"))
    assert check(case(tools_any_of=options), say("ok", tools=(tool("search_products"),))).passed
    assert not check(case(tools_any_of=options), say("ok")).passed
    assert not check(
        case(tools_forbidden=("get_order_status",)), say("ok", tools=(tool("get_order_status"),))
    ).passed
    assert not check(case(tools_none=True), say("ok", tools=(tool("get_product"),))).passed
    assert check(case(tools_none=True), say("ok")).passed


def test_contains_uses_alternatives_and_ignores_accents() -> None:
    hard = case(contains=((r"R\$\s?2\.199,00",), ("esgotad", "indisponi")))
    assert check(hard, say("O preço é R$ 2.199,00 e está indisponível.")).passed
    assert check(hard, say("Sai por R$\xa02.199,00. Esgotado!")).passed
    missing = check(hard, say("Sai por R$ 2.199,00."))
    assert not missing.passed and "esgotad" in missing.hard[0]


def test_contains_applies_to_the_last_answer_and_conversation_to_any() -> None:
    hard = case(contains=(("final",),), contains_in_conversation=(("pix",),))
    assert check(hard, say("no pix fica 10", "resposta final")).passed
    assert not check(hard, say("resposta final", "outra coisa")).passed
    assert not check(case(contains=(("final",),)), say("final", "outra")).passed


def test_forbidden_terms_are_searched_in_the_whole_conversation() -> None:
    hard = case(forbidden=(r"1\.935", "(?<!nao )estamos abertos"))
    assert check(hard, say("Hoje não estamos abertos.")).passed
    assert not check(hard, say("Sai por R$ 1.935,12", "ok")).passed
    assert not check(hard, say("ok", "Estamos abertos!")).passed


def test_a_term_may_only_appear_in_a_sentence_that_says_it_is_unavailable() -> None:
    hard = case(only_if_unavailable=("GF-3D",))
    assert check(hard, say("O GF-3D está esgotado. Temos outros.")).passed
    assert not check(hard, say("O GF-3D é ótimo. Está esgotado o outro.")).passed


def test_robotic_tone_fails_any_case() -> None:
    assert not check(case(), say("Prezado cliente, segue o preço.")).passed
    assert not check(case(), say("Como modelo de linguagem, não posso.")).passed


def test_fallback_and_internal_errors_fail_any_case() -> None:
    assert not check(case(), say("segura", fallback=True)).passed
    assert not check(
        case(), say("ok", tools=(tool("get_product", ok=False, error="internal_error"),))
    ).passed


def test_provider_error_fails_with_the_reason() -> None:
    verdict = check(case(), Conversation((), error="ProviderUnavailable: 502"))
    assert verdict.hard == ["o provedor falhou: ProviderUnavailable: 502"]


def test_soft_checks_report_without_failing(facts: Facts) -> None:
    soft_case = parse_case(
        {"id": "s", "intent": "i", "turns": ["oi"], "soft": {"phrases": [["alternativ"]]}}, facts
    )
    verdict = check(
        soft_case,
        say(
            "ok",
            guard_blocked=True,
            tools=(tool("get_product", ok=False, error="invalid_arguments"),),
        ),
    )
    assert verdict.passed
    joined = " ".join(verdict.soft)
    assert "alternativ" in joined and "guarda barrou" in joined and "get_product" in joined
    assert check(soft_case, say("há alternativas")).soft == []


def test_blocked_answer_that_was_corrected_is_only_a_warning() -> None:
    verdict = check(case(), say("ok", guard_blocked=True))
    assert verdict.passed and any("guarda" in s for s in verdict.soft)
