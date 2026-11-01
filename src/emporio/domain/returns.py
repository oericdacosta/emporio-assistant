"""Elegibilidade de devolução, troca e garantia.

O LLM nunca calcula prazo: ele recebe o veredito pronto, e ``today`` vem do ``Clock``.
Os prazos contam a partir do recebimento. A loja só guarda a data prevista de entrega
(``estimated_delivery``); nos pedidos entregues ela serve de aproximação do recebimento, e o
veredito avisa isso em ``basis`` para o agente explicar a suposição ao cliente. A data
que o próprio cliente informa (``received_on``) tem precedência. O dia do recebimento é o
dia zero, e o sétimo dia ainda vale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final

from emporio.domain.models import OrderStatus
from emporio.domain.rules import RULES

REGRET_DAYS: Final = RULES.regret_days  # arrependimento e preferência, a partir do recebimento
DEFECT_EXCHANGE_DAYS: Final = (
    RULES.defect_exchange_days
)  # defeito: troca na loja, a partir da COMPRA
LEGAL_WARRANTY_DAYS: Final = RULES.legal_warranty_days  # garantia legal, a partir do recebimento


class ReturnReason(StrEnum):
    REGRET = "regret"  # arrependimento
    DEFECT = "defect"
    PREFERENCE = "preference"  # troca por cor/modelo/tamanho


class Verdict(StrEnum):
    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    LEGAL_WARRANTY = "legal_warranty"  # fora dos 30 d, dentro dos 90 d: encaminhar
    NOT_APPLICABLE = "not_applicable"  # sem produto entregue (cancelado/em andamento)
    ESCALATE = "escalate"  # sem dados suficientes: encaminhar à equipe


class Basis(StrEnum):
    CUSTOMER_RECEIVED_ON = "customer_received_on"  # data informada, não verificada
    ESTIMATED_DELIVERY = "estimated_delivery"  # proxy: a loja não registra a data real
    PURCHASE_DATE = "purchase_date"
    ORDER_STATUS = "order_status"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ReturnVerdict:
    verdict: Verdict
    reason: ReturnReason
    basis: Basis
    explanation: str
    deadline: date | None = None
    days_left: int | None = None  # negativo = dias de atraso
    warnings: tuple[str, ...] = ()


def _months(n: int) -> str:
    return f"{n // 12} ano{'s' if n > 12 else ''}" if n % 12 == 0 else f"{n} meses"


_MANUFACTURER_WARRANTY: Final = " a ".join(map(_months, RULES.manufacturer_warranty_months))
_PACKAGING: Final = "Produto na embalagem original, sem sinais de uso, com acessórios e manuais."
_EXCLUSIONS: Final = (
    "Não são elegíveis: itens personalizados/sob encomenda, 'venda final' e boquilhas "
    "(a loja não tem essa marcação nos dados: confirmar com o cliente e encaminhar)."
)
_REFUND: Final = (
    f"Reembolso na mesma forma de pagamento em até {RULES.refund_business_days} dias úteis; "
    "frete de devolução por conta da loja."
)
_PREFERENCE: Final = "Troca sujeita à disponibilidade; diferença de valor cobrada ou reembolsada."


def _verdict(
    verdict: Verdict,
    reason: ReturnReason,
    basis: Basis,
    explanation: str,
    *,
    deadline: date | None = None,
    today: date | None = None,
    warnings: tuple[str, ...] = (),
) -> ReturnVerdict:
    left = (deadline - today).days if deadline and today else None
    return ReturnVerdict(verdict, reason, basis, explanation, deadline, left, warnings)


def check_return(
    *,
    status: OrderStatus,
    order_date: date,
    estimated_delivery: date | None,
    reason: ReturnReason,
    today: date,
    received_on: date | None = None,
) -> ReturnVerdict:
    """Veredito de devolução para um pedido. ``received_on`` é a data informada pelo cliente."""
    if received_on is not None and not order_date <= received_on <= today:
        raise ValueError("received_on deve estar entre a data do pedido e hoje")

    na = Verdict.NOT_APPLICABLE
    if status is OrderStatus.CANCELLED:
        return _verdict(
            na, reason, Basis.ORDER_STATUS, "Pedido cancelado: não há produto para devolver."
        )
    if status in (OrderStatus.PENDING, OrderStatus.CONFIRMED):
        return _verdict(
            na,
            reason,
            Basis.ORDER_STATUS,
            f"Pedido ainda não enviado (status {status.value}): não há devolução; "
            "o cancelamento é tratado pelo atendimento.",
        )
    if status is OrderStatus.SHIPPED and received_on is None:
        return _verdict(
            na,
            reason,
            Basis.ORDER_STATUS,
            "Pedido em trânsito, ainda não recebido: o prazo só começa no recebimento. "
            "Se o cliente já recebeu, pedir a data de recebimento.",
        )

    if received_on is not None:
        receipt, basis = received_on, Basis.CUSTOMER_RECEIVED_ON
    elif estimated_delivery is not None:
        receipt, basis = estimated_delivery, Basis.ESTIMATED_DELIVERY
    else:
        receipt, basis = None, Basis.NONE

    if reason is ReturnReason.DEFECT:
        return _check_defect(order_date, receipt, basis, today)

    if receipt is None:
        return _verdict(
            Verdict.ESCALATE, reason, basis, "Sem data de entrega no sistema: consultar a gerência."
        )
    deadline = receipt + timedelta(days=REGRET_DAYS)
    warnings = (_PACKAGING, _EXCLUSIONS, _REFUND if reason is ReturnReason.REGRET else _PREFERENCE)
    if today <= deadline:
        return _verdict(
            Verdict.ELIGIBLE,
            reason,
            basis,
            f"Dentro do prazo de {REGRET_DAYS} dias corridos do recebimento (até {deadline:%d/%m/%Y}).",
            deadline=deadline,
            today=today,
            warnings=warnings,
        )
    return _verdict(
        Verdict.NOT_ELIGIBLE,
        reason,
        basis,
        f"Prazo de {REGRET_DAYS} dias corridos vencido em {deadline:%d/%m/%Y}.",
        deadline=deadline,
        today=today,
        warnings=warnings,
    )


def _check_defect(
    order_date: date, receipt: date | None, basis: Basis, today: date
) -> ReturnVerdict:
    reason = ReturnReason.DEFECT
    exchange_deadline = order_date + timedelta(days=DEFECT_EXCHANGE_DAYS)
    if today <= exchange_deadline:
        return _verdict(
            Verdict.ELIGIBLE,
            reason,
            Basis.PURCHASE_DATE,
            f"Defeito de fabricação dentro de {DEFECT_EXCHANGE_DAYS} dias corridos da compra "
            f"(até {exchange_deadline:%d/%m/%Y}): troca na loja.",
            deadline=exchange_deadline,
            today=today,
            warnings=("Mau uso, quedas, umidade e modificações não autorizadas não são cobertos.",),
        )
    if receipt is None:
        return _verdict(
            Verdict.ESCALATE,
            reason,
            basis,
            "Passou de 30 dias da compra e não há data de entrega no sistema: consultar a gerência.",
        )
    warranty_deadline = receipt + timedelta(days=LEGAL_WARRANTY_DAYS)
    if today <= warranty_deadline:
        return _verdict(
            Verdict.LEGAL_WARRANTY,
            reason,
            basis,
            f"Passou da troca de {DEFECT_EXCHANGE_DAYS} dias, mas está na garantia legal de "
            f"{LEGAL_WARRANTY_DAYS} dias do recebimento (até {warranty_deadline:%d/%m/%Y}): "
            "encaminhar ao atendimento.",
            deadline=warranty_deadline,
            today=today,
        )
    return _verdict(
        Verdict.NOT_ELIGIBLE,
        reason,
        basis,
        "Fora da troca (30 dias da compra) e da garantia legal (90 dias do recebimento): "
        f"acionar a garantia do fabricante ({_MANUFACTURER_WARRANTY}); a loja pode intermediar.",
        deadline=warranty_deadline,
        today=today,
    )
