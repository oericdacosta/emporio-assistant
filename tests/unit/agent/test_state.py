from __future__ import annotations

from typing import Any

from emporio.agent.graph import Agent
from emporio.agent.prompt import build_system_prompt
from emporio.agent.session import Session
from emporio.agent.state import NO_ORDER_HINT, WorkingState, compose
from emporio.agent.types import Outcome
from emporio.clock import FixedClock
from emporio.domain.hours import store_status
from support.fakes import FakeLLM, StubToolbox, say, use


def product(pid: int, name: str, price: str) -> dict[str, Any]:
    return {"ok": True, "data": {"product_id": pid, "name": name, "effective_price_brl": price}}


def test_products_orders_ticket_and_offers_come_from_tool_results() -> None:
    w = WorkingState()
    w.absorb("get_product", {"name": "f310"}, product(87, "Yamaha F310", "R$ 699,90"))
    w.absorb(
        "search_products",
        {},
        {"ok": True, "data": {"items": [product(5, "Outro", "R$ 10,00")["data"]]}},
    )
    w.absorb("verify_customer", {"order_id": 9, "proof": "x"}, {"ok": True, "data": {}})
    w.absorb("verify_customer", {"order_id": 9, "proof": "x"}, {"ok": True, "data": {}})
    w.absorb("register_complaint", {}, {"ok": True, "data": {"ticket_id": "EMP-ABCDEF12"}})
    w.offers_made.append("parcelamento")
    assert w.summary_lines() == [
        "Produtos já consultados: Yamaha F310 (id 87) R$ 699,90; Outro (id 5) R$ 10,00",
        "Produtos que o cliente perguntou, em ordem: 1º Yamaha F310",
        "Pedidos verificados nesta conversa: 9",
        "Protocolo de reclamação já registrado: EMP-ABCDEF12",
        "Ofertas já feitas ao cliente (não repita): parcelamento",
    ]


def test_failed_calls_do_not_enter_the_state_and_are_counted() -> None:
    w = WorkingState()
    denied = {"ok": False, "error": {"code": "not_verified", "message": "", "hint": ""}}
    w.absorb("verify_customer", {"order_id": 9}, denied)
    w.absorb("verify_customer", {"order_id": 9}, denied)
    assert w.verified_orders == [] and w.failures == {("verify_customer", "not_verified"): 2}
    w.note_result("verify_customer", True)
    assert w.failures == {}


def test_only_the_eight_most_recent_products_are_shown() -> None:
    w = WorkingState()
    for i in range(1, 11):
        w.absorb("get_product", {}, product(i, f"P{i}", "R$ 1,00"))
    w.absorb("get_product", {}, product(1, "P1", "R$ 1,00"))  # volta a ser o mais recente
    line = w.summary_lines()[0]
    assert line.count("(id ") == 8 and "(id 1)" in line and "(id 2)" not in line


def test_summary_goes_inside_the_context_block_at_the_end() -> None:
    now = FixedClock.parse("2026-03-23T10:00:00").now()
    text = build_system_prompt(now, store_status(now), None, ["Linha X"])
    assert text.index("Linha X") < text.index("</contexto_atual>")
    assert text.rstrip().endswith("</contexto_atual>") or "</contexto_atual>" in text[-200:]
    assert build_system_prompt(now, store_status(now)) == build_system_prompt(
        now, store_status(now), None, ()
    )


def test_compose_removes_a_repeated_offer_and_registers_new_ones() -> None:
    w = WorkingState()
    text = "Sai por R$ 100,00. Quer ver o parcelamento?"
    assert compose(text, w) == (text, 0) and w.offers_made == ["parcelamento"]
    assert compose("Sai por R$ 100,00.\nQuer ver o parcelamento? 🎸", w) == (
        "Sai por R$ 100,00.",
        1,
    )
    # só a frase do gancho: nunca deixa vazio
    assert compose("Quer ver o parcelamento?", w) == ("Quer ver o parcelamento?", 0)
    # pergunta que não é oferta conhecida fica
    ask = "Anotado. Qual estilo você procura?"
    assert compose(ask, w) == (ask, 0)
    # só a ÚLTIMA frase conta
    mid = "Quer o WhatsApp? Sai por R$ 1.000,00."
    assert compose(mid, w) == (mid, 0) and "WhatsApp" not in w.offers_made


def test_compose_classifies_every_offer_kind() -> None:
    cases = {
        "Posso ajudar em mais alguma coisa?": "mais alguma coisa",
        "Precisa de algo mais?": "mais alguma coisa",
        "Quer ver mais opções?": "ver mais opções",
        "Quer saber as formas de pagamento?": "formas de pagamento",
        "Prefere falar no WhatsApp?": "WhatsApp",
    }
    for question, label in cases.items():
        w = WorkingState()
        text = f"Pronto. {question}"
        assert compose(text, w) == (text, 0) and w.offers_made == [label]
        assert compose(text, w) == ("Pronto.", 1)


def test_the_state_is_in_memory_only_and_fed_to_the_prompt() -> None:
    box = StubToolbox({"get_product": product(87, "Yamaha F310", "R$ 699,90")})
    seen: list[str] = []

    def system(session: Session) -> str:
        seen.append("|".join(session.working.summary_lines()))
        return "SISTEMA"

    llm = FakeLLM(
        use("get_product", name="f310"),
        say("A Yamaha F310 sai por R$ 699,90. Quer ver o parcelamento?"),
        say("Claro, a Yamaha F310 sai por R$ 699,90. Quer ver o parcelamento?"),
    )
    agent: Agent[str] = Agent(llm, box, lambda s: "ctx", system)
    session = Session("s1")
    agent.run_turn(session, "quanto custa a f310?")
    assert seen[0] == "" and "Yamaha F310 (id 87) R$ 699,90" in seen[1]
    result = agent.run_turn(session, "e o preço de novo?")
    assert result.text == "Claro, a Yamaha F310 sai por R$ 699,90." and result.offers_removed == 1
    assert result.working_products == 1 and result.outcome is Outcome.ANSWERED
    assert "699" not in "".join(m.content for m in session.history if m.role == "user")


def test_run_turn_sets_the_current_text_and_make_ctx_sees_it() -> None:
    seen: list[tuple[str, tuple[str, ...]]] = []

    def make_ctx(session: Session) -> str:
        seen.append((session.current_text, session.user_texts()))
        return "ctx"

    agent: Agent[str] = Agent(FakeLLM(say("Oi, tudo bem?")), StubToolbox(), make_ctx, lambda s: "S")
    session = Session("s1")
    agent.run_turn(session, "  oi  ")
    assert ("oi", ("oi",)) in seen


# --- disjuntor, reclamação com estado e dica do pedido perdido ----------------------------------

NEEDS_CONTACT = {
    "ok": False,
    "error": {"code": "contact_needed", "message": "m", "hint": "h"},
}


def test_breaker_blocks_the_third_identical_failure_and_success_resets() -> None:
    box = StubToolbox({"register_complaint": NEEDS_CONTACT})
    attempt = use("register_complaint", summary="defeito")
    llm = FakeLLM(attempt, attempt, attempt, say("Preciso de um contato para registrar."))
    agent: Agent[str] = Agent(llm, box, lambda s: "ctx", lambda s: "SISTEMA", fact_detector=None)
    session = Session("s1")
    result = agent.run_turn(session, "o ukulele veio com defeito")
    assert [t.error_code for t in result.tools] == [
        "contact_needed",
        "contact_needed",
        "repeated_failure",
    ]
    assert len(box.calls) == 2  # a terceira não foi executada
    session.working.note_result("register_complaint", True)
    assert not session.working.tripped("register_complaint")


def test_breaker_holds_across_turns() -> None:
    w = WorkingState()
    for _ in range(2):
        w.absorb("register_complaint", {"summary": "x"}, NEEDS_CONTACT)
    assert w.tripped("register_complaint") and not w.tripped("get_product")
    assert w.pending_complaint == {"summary": "x", "order_id": None}


def test_lost_order_number_hint_needs_defect_and_no_order_and_nothing_registered() -> None:
    w = WorkingState()
    texts = ["meu ukulele chegou com defeito", "não tenho o número do pedido"]
    assert w.summary_lines(texts)[0].startswith("Sistema: o cliente não tem o número do pedido")
    assert w.summary_lines(texts[:1]) == [] and w.summary_lines(texts[1:]) == []
    w.verified_orders.append(9)
    assert all("Sistema:" not in line for line in w.summary_lines(texts))
    w.verified_orders.clear()
    w.complaint_ticket = "EMP-ABCDEF12"
    assert all("Sistema:" not in line for line in w.summary_lines(texts))


def ask(w: WorkingState, tool: str, args: dict[str, Any], name: str) -> None:
    key = "product" if tool == "calculate_payment_options" else "name"
    w.absorb(tool, args, {"ok": True, "data": {key: name, "name": name, "product_id": 1}})


def test_asked_products_keep_the_order_the_customer_asked_in() -> None:
    """M01: Kalani, Kala KA-15S, baixo...; search_products é listagem e não conta."""
    w = WorkingState()
    ask(w, "get_product", {"name": "kalani"}, "Kalani KAL-300S")
    ask(w, "get_product", {"name": "ka-15s"}, "Kala KA-15S")
    w.absorb(
        "search_products",
        {},
        {"ok": True, "data": {"items": [product(5, "Listado", "R$ 1,00")["data"]]}},
    )
    ask(w, "calculate_payment_options", {"product_id": 7}, "Baixo Tagima")
    ask(w, "get_product", {"name": "kalani"}, "Kalani KAL-300S")  # repetido: mantém a 1ª posição
    w.absorb("get_product", {"name": "x"}, {"ok": True, "data": {"candidates": []}})  # ambíguo
    assert w.asked_products == ["Kalani KAL-300S", "Kala KA-15S", "Baixo Tagima"]
    assert w.summary_lines()[1] == (
        "Produtos que o cliente perguntou, em ordem: 1º Kalani KAL-300S; 2º Kala KA-15S; "
        "3º Baixo Tagima"
    )


def test_the_first_asked_product_survives_many_products() -> None:
    w = WorkingState()
    for i in range(1, 21):
        ask(w, "get_product", {"product_id": i}, f"P{i}")
    line = next(x for x in w.summary_lines() if x.startswith("Produtos que o cliente"))
    assert "1º P1;" in line and "4º P4; ..." in line and "20º P20" in line
    assert "5º P5" not in line and "15º P15" in line and "14º" not in line


def test_complaint_detection_keeps_the_first_complaint_without_personal_data() -> None:
    w = WorkingState()
    w.note_user_text("Oi, tudo bem?")
    assert w.complaint_summary is None
    w.note_user_text("O teclado que comprei não liga, chegou assim. Meu e-mail ana@x.com")
    w.note_user_text("Além disso veio riscado")
    assert w.complaint_summary == "O teclado que comprei não liga, chegou assim. Meu e-mail [email]"
    w.absorb("register_complaint", {}, {"ok": True, "data": {"ticket_id": "EMP-ABCDEF12"}})
    assert w.complaint_registered and w.complaint_to_register("a@b.com") is None


def test_lost_order_number_hint_reads_the_real_c01_sentences() -> None:
    w = WorkingState()
    texts = ("O teclado que comprei não liga, chegou assim.", "Não sei o número do pedido")
    assert NO_ORDER_HINT in w.summary_lines(texts)
    assert NO_ORDER_HINT not in w.summary_lines(texts[1:])
    assert NO_ORDER_HINT in w.summary_lines(
        ("A tarraxa chegou quebrada", "perdi o pedido, sem o número do pedido")
    )


def test_an_order_reference_without_complaint_words_is_a_verification_not_a_complaint() -> None:
    w = WorkingState()
    w.note_user_text("O teclado não liga")
    assert w.complaint_to_register("Pedido 4, lucas.mendes@jmail.com") is None
    assert w.complaint_to_register("pode me ligar em (67) 98888-7777") is not None


def test_last_order_id_follows_the_customer_and_survives_turns_without_order() -> None:
    working = WorkingState()
    working.note_user_text("Pedido 3, isabela.moreira@jmail.com")
    working.note_user_text("Agora o pedido 4")
    working.note_user_text("O e-mail é lucas.mendes@jmail.com")
    assert working.last_order_id == 4


def test_verification_needs_the_proof_in_the_current_text_and_an_unverified_order() -> None:
    working = WorkingState()
    working.note_user_text("Agora o pedido 4")
    assert working.verification_to_run("Agora o pedido 4") is None  # sem prova
    text = "O e-mail é lucas.mendes@jmail.com"
    working.note_user_text(text)
    assert working.verification_to_run(text) == {"order_id": 4, "proof": "lucas.mendes@jmail.com"}
    assert working.verification_to_run("não tenho o e-mail") is None  # prova antiga não vale
    working.verified_orders.append(4)
    assert working.verification_to_run(text) is None  # já verificado
