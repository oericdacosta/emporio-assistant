from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from emporio.agent.session import (
    WINDOW,
    Session,
    SQLiteSessionStore,
    default_history_path,
    new_session_id,
)
from emporio.clock import FixedClock
from emporio.llm.base import Message, ToolCall


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock.parse("2026-03-23T10:00:00")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "history" / "sessions.db"


@pytest.fixture
def store(db: Path, clock: FixedClock) -> Iterator[SQLiteSessionStore]:
    opened = SQLiteSessionStore(db, clock=clock)
    yield opened
    opened.close()


def turn(user: str, assistant: str) -> list[Message]:
    return [Message("user", user), Message("assistant", assistant)]


def test_unknown_session_is_empty_and_unverified(store: SQLiteSessionStore) -> None:
    session = store.load("nova")
    assert session.id == "nova" and session.history == []
    assert session.guard.customer_id is None and session.approved_answers == []


def test_saved_text_comes_back_in_order(store: SQLiteSessionStore) -> None:
    session = Session("s1")
    store.save(session, turn("quanto custa o gd20?", "R$ 2.199,00"))
    store.save(session, turn("e no pix?", "R$ 2.089,05"))
    loaded = store.load("s1")
    assert [m.content for m in loaded.history] == [
        "quanto custa o gd20?",
        "R$ 2.199,00",
        "e no pix?",
        "R$ 2.089,05",
    ]
    assert loaded.approved_answers == ["R$ 2.199,00", "R$ 2.089,05"]


def test_personal_data_is_masked_before_it_is_stored(store: SQLiteSessionStore) -> None:
    store.save(
        Session("s1"),
        turn(
            "meu cpf é 123.456.789-09, email ana@jmail.com, fone (67) 99812-3456, cartão 4111 1111 1111 1111",
            "Anotado, Ana.",
        ),
    )
    stored = store.load("s1").history[0].content
    assert stored == "meu cpf é [cpf], email [email], fone [telefone], cartão [cartao]"


def test_only_user_and_assistant_text_is_stored(store: SQLiteSessionStore, db: Path) -> None:
    call = ToolCall("c1", "get_order_status", {"order_id": 4})
    store.save(
        Session("s1"),
        [
            Message("system", "prompt secreto"),
            Message("user", "status do pedido 4"),
            Message("assistant", "", (call,)),
            Message("tool", '{"tracking_code": "BRAB1234567BR"}', tool_call_id="c1"),
            Message("assistant", "Está a caminho."),
            Message("user", ""),
        ],
    )
    assert [m.content for m in store.load("s1").history] == [
        "status do pedido 4",
        "Está a caminho.",
    ]
    dump = "\n".join(store._db.iterdump())
    assert "prompt secreto" not in dump and "BRAB1234567BR" not in dump


def test_verification_state_is_never_stored(store: SQLiteSessionStore) -> None:
    session = Session("s1")
    session.guard.customer_id = 7
    session.guard.failures = 2
    store.save(session, turn("oi", "olá"))
    columns = [row[1] for row in store._db.execute("PRAGMA table_info(messages)")]
    assert columns == ["id", "session_id", "role", "content", "created_at"]
    dump = "\n".join(store._db.iterdump()).lower()
    assert "customer" not in dump and "failures" not in dump and "verified" not in dump
    assert store.load("s1").guard.customer_id is None  # ao retomar, verifica de novo


def test_window_has_the_last_messages_and_starts_with_the_customer(
    store: SQLiteSessionStore,
) -> None:
    session = Session("s1")
    for i in range(15):
        store.save(session, turn(f"pergunta {i}", f"resposta {i}"))
    history = store.load("s1").history
    assert len(history) == WINDOW and history[0].role == "user"
    assert history[0].content == "pergunta 5" and history[-1].content == "resposta 14"

    odd = Session("s2")
    store.save(odd, [Message("assistant", "sobra do corte")])
    for i in range(10):
        store.save(odd, turn(f"p{i}", f"r{i}"))
    loaded = store.load("s2").history
    assert loaded[0] == Message("user", "p0") and len(loaded) == 20


def test_window_cut_never_starts_on_an_assistant_message(store: SQLiteSessionStore) -> None:
    session = Session("s1")
    for i in range(11):
        store.save(session, turn(f"p{i}", f"r{i}"))
    store.save(session, [Message("user", "última")])
    history = store.load("s1").history
    assert history[0].role == "user" and len(history) <= WINDOW


def test_delete_clears_only_that_session(store: SQLiteSessionStore) -> None:
    store.save(Session("a"), turn("oi", "olá"))
    store.save(Session("b"), turn("oi", "olá"))
    assert store.delete("a") == 2
    assert store.load("a").history == []
    assert len(store.load("b").history) == 2
    assert store.delete("a") == 0


def test_purge_removes_messages_past_the_retention_period(
    store: SQLiteSessionStore, clock: FixedClock
) -> None:
    store.save(Session("velha"), turn("oi", "olá"))
    clock.advance(days=29)
    store.save(Session("recente"), turn("oi", "olá"))
    assert store.purge() == 0
    clock.advance(days=2)  # a velha tem 31 dias, a recente 2
    assert store.purge() == 2
    assert store.load("velha").history == [] and len(store.load("recente").history) == 2


def test_purge_runs_when_the_store_is_opened(db: Path, clock: FixedClock) -> None:
    first = SQLiteSessionStore(db, clock=clock)
    first.save(Session("s1"), turn("oi", "olá"))
    first.close()
    clock.advance(days=31)
    reopened = SQLiteSessionStore(db, clock=clock)
    assert reopened.load("s1").history == []
    reopened.close()


def test_history_survives_reopening_the_store(db: Path, clock: FixedClock) -> None:
    first = SQLiteSessionStore(db, clock=clock)
    first.save(Session("s1"), turn("oi", "olá"))
    first.close()
    reopened = SQLiteSessionStore(db, clock=clock)
    assert [m.content for m in reopened.load("s1").history] == ["oi", "olá"]
    reopened.close()


def test_session_ids_and_default_path() -> None:
    assert len(new_session_id()) == 8 and new_session_id() != new_session_id()
    assert default_history_path({}) == Path("history/sessions.db")
    assert default_history_path({"EMPORIO_HISTORY_DB": "dados/x.db"}) == Path("dados/x.db")
