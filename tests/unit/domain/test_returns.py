from __future__ import annotations

from datetime import date

import pytest

from emporio.domain.models import OrderStatus
from emporio.domain.returns import Basis, ReturnReason, ReturnVerdict, Verdict, check_return

REGRET, DEFECT, PREF = ReturnReason.REGRET, ReturnReason.DEFECT, ReturnReason.PREFERENCE

# Datas reais dos CSVs: uma compra em 2025-12-10 com entrega prevista em 2025-12-22 e outra
# em 2026-02-03 com entrega prevista em 2026-02-17.
ORDER_DEC_2025 = {
    "status": OrderStatus.DELIVERED,
    "order_date": date(2025, 12, 10),
    "estimated_delivery": date(2025, 12, 22),
}
ORDER_FEB_2026 = {
    "status": OrderStatus.DELIVERED,
    "order_date": date(2026, 2, 3),
    "estimated_delivery": date(2026, 2, 17),
}


def run(
    order: dict[str, object], reason: ReturnReason, today: date, **extra: date
) -> ReturnVerdict:
    return check_return(reason=reason, today=today, **order, **extra)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("today", "verdict", "days_left"),
    [
        (date(2025, 12, 22), Verdict.ELIGIBLE, 7),  # dia do recebimento (pela previsão)
        (date(2025, 12, 29), Verdict.ELIGIBLE, 0),  # o sétimo dia ainda vale
        (date(2025, 12, 30), Verdict.NOT_ELIGIBLE, -1),  # o oitavo dia já não
        (date(2026, 10, 31), Verdict.NOT_ELIGIBLE, -306),  # bem depois do prazo
    ],
)
def test_regret_uses_estimated_delivery_as_receipt_proxy(
    today: date, verdict: Verdict, days_left: int
) -> None:
    result = run(ORDER_DEC_2025, REGRET, today)
    assert (result.verdict, result.days_left) == (verdict, days_left)
    assert result.basis is Basis.ESTIMATED_DELIVERY and result.deadline == date(2025, 12, 29)


def test_pedido_1_regret_on_project_today_is_denied_with_nov_2025_deadline() -> None:
    result = check_return(
        status=OrderStatus.DELIVERED,
        order_date=date(2025, 10, 15),
        estimated_delivery=date(2025, 10, 25),
        reason=REGRET,
        today=date(2026, 10, 31),
    )
    assert result.verdict is Verdict.NOT_ELIGIBLE and result.deadline == date(2025, 11, 1)


def test_customer_received_date_overrides_estimate() -> None:
    late = run(ORDER_FEB_2026, REGRET, date(2026, 3, 1), received_on=date(2026, 2, 25))
    assert (late.verdict, late.basis, late.deadline) == (
        Verdict.ELIGIBLE,
        Basis.CUSTOMER_RECEIVED_ON,
        date(2026, 3, 4),
    )
    assert (
        run(ORDER_FEB_2026, REGRET, date(2026, 3, 1)).verdict is Verdict.NOT_ELIGIBLE
    )  # pela previsão, venceu em 24/02


def test_regret_warnings_cover_packaging_exclusions_and_refund() -> None:
    warnings = " ".join(run(ORDER_FEB_2026, REGRET, date(2026, 2, 20)).warnings)
    assert (
        "embalagem original" in warnings and "boquilhas" in warnings and "10 dias úteis" in warnings
    )
    assert "disponibilidade" in " ".join(run(ORDER_FEB_2026, PREF, date(2026, 2, 20)).warnings)


@pytest.mark.parametrize(
    ("today", "verdict"),
    [
        (date(2026, 3, 5), Verdict.ELIGIBLE),  # compra + 30
        (date(2026, 3, 6), Verdict.LEGAL_WARRANTY),  # 31 d da compra, dentro de 90 d do recebimento
        (date(2026, 5, 18), Verdict.LEGAL_WARRANTY),  # previsão 17/02 + 90
        (date(2026, 5, 19), Verdict.NOT_ELIGIBLE),  # garantia do fabricante
    ],
)
def test_defect_three_bands(today: date, verdict: Verdict) -> None:
    assert run(ORDER_FEB_2026, DEFECT, today).verdict is verdict


def test_defect_within_30_days_is_anchored_on_purchase_date() -> None:
    result = run(ORDER_FEB_2026, DEFECT, date(2026, 2, 20))
    assert (
        result.basis is Basis.PURCHASE_DATE
        and result.deadline == date(2026, 3, 5)
        and result.days_left == 13
    )


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (OrderStatus.CANCELLED, "cancelado"),
        (OrderStatus.PENDING, "não enviado"),
        (OrderStatus.CONFIRMED, "não enviado"),
        (OrderStatus.SHIPPED, "em trânsito"),  # ainda não foi recebido
    ],
)
@pytest.mark.parametrize("reason", list(ReturnReason))
def test_not_delivered_is_not_applicable(
    status: OrderStatus, needle: str, reason: ReturnReason
) -> None:
    result = check_return(
        status=status,
        order_date=date(2026, 2, 15),
        estimated_delivery=date(2026, 2, 28),
        reason=reason,
        today=date(2026, 3, 22),
    )
    assert result.verdict is Verdict.NOT_APPLICABLE and needle in result.explanation.lower()


def test_shipped_with_customer_receipt_date_is_evaluated() -> None:
    result = check_return(
        status=OrderStatus.SHIPPED,
        order_date=date(2026, 2, 15),
        estimated_delivery=date(2026, 2, 28),
        reason=REGRET,
        today=date(2026, 3, 3),
        received_on=date(2026, 3, 1),
    )
    assert result.verdict is Verdict.ELIGIBLE


def test_missing_receipt_data_escalates() -> None:
    base = {
        "status": OrderStatus.DELIVERED,
        "order_date": date(2026, 1, 1),
        "estimated_delivery": None,
    }
    assert run(base, REGRET, date(2026, 1, 5)).verdict is Verdict.ESCALATE
    assert run(base, DEFECT, date(2026, 1, 20)).verdict is Verdict.ELIGIBLE  # só precisa da compra
    assert run(base, DEFECT, date(2026, 3, 1)).verdict is Verdict.ESCALATE


@pytest.mark.parametrize("received", [date(2026, 2, 2), date(2026, 3, 10)])
def test_received_on_must_be_between_order_and_today(received: date) -> None:
    with pytest.raises(ValueError, match="received_on"):
        run(ORDER_FEB_2026, REGRET, date(2026, 3, 1), received_on=received)
