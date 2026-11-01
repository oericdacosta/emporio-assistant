"""Estado de trabalho da conversa: o que o orquestrador sabe, sem depender do modelo.

Vive só em memória, dentro do ``Session``, e nunca vai ao SQLite nem ao turno gravado. É
atualizado a partir dos resultados das tools (``absorb``) e do fechamento da resposta
(``compose``), e vira linhas curtas no ``<contexto_atual>`` do prompt. Só ids, nomes de produto e
preços: nenhum dado pessoal.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from emporio.domain.complaint import COMPLAINT_WORDS, fold
from emporio.domain.identity_slots import extract_order_id, extract_proof
from emporio.privacy import redact

SHOWN_PRODUCTS: Final = 8
ASKED_SHOWN: Final = 12  # acima disso: os 4 primeiros e os 6 mais recentes
ASKED_HEAD: Final = 4
ASKED_TAIL: Final = 6
SUMMARY_CHARS: Final = 300

# oferta -> como ela aparece numa pergunta final. A chave é o texto mostrado no prompt.
OFFERS: Final = {
    "parcelamento": re.compile(r"parcel", re.IGNORECASE),
    "formas de pagamento": re.compile(
        r"formas? de pagamento|condi[cç][õo]es de pagamento|como pagar", re.IGNORECASE
    ),
    "WhatsApp": re.compile(r"whats\s?app", re.IGNORECASE),
    "mais alguma coisa": re.compile(
        r"mais alguma (?:coisa|d[úu]vida)|algo mais|mais algo", re.IGNORECASE
    ),
    "ver mais opções": re.compile(r"(?:mais|outras) op[cç][õo]es|ver mais", re.IGNORECASE),
}
# Disjuntor: depois de tantas falhas seguidas de (tool, código), a tool não é chamada de novo.
# verify_customer fica de fora: o próprio verificador de identidade já tem bloqueio por pedido.
MAX_REPEATED_FAILURES: Final = 2
BREAKER_EXEMPT: Final = frozenset({"verify_customer"})

_NO_ORDER_NUMBER: Final = re.compile(
    r"\b(?:nao (?:sei|tenho|lembro|acho|encontro)|perdi|esqueci)\b.{0,40}\b(?:numero|pedido)"
    r"|\bsem (?:o )?numero\b.{0,20}\bpedido"
)
_ORDER_REF: Final = re.compile(r"\bpedido\s*(?:n\S{0,2}\s*)?#?\d+")
NO_ORDER_HINT: Final = (
    "Sistema: o cliente não tem o número do pedido; ofereça registrar a reclamação com um "
    "contato (e-mail ou telefone)"
)
_BOUNDARY: Final = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True, slots=True)
class ProductNote:
    name: str
    price: str  # preço vigente, no texto que a tool devolveu


@dataclass(slots=True)
class WorkingState:
    products: dict[int, ProductNote] = field(default_factory=dict)  # do mais antigo ao mais novo
    verified_orders: list[int] = field(default_factory=list)
    complaint_ticket: str | None = None
    offers_made: list[str] = field(default_factory=list)
    failures: dict[tuple[str, str], int] = field(default_factory=dict)  # (tool, código) -> vezes
    pending_complaint: dict[str, Any] | None = None  # summary e order_id, à espera de um contato
    complaint_summary: str | None = None  # a queixa do cliente (sem dado pessoal), sem protocolo
    asked_products: list[str] = field(default_factory=list)  # nomes, na ordem em que perguntou
    last_order_id: int | None = None  # o último pedido que o cliente citou, em qualquer turno

    @property
    def complaint_registered(self) -> bool:
        return self.complaint_ticket is not None

    def note_user_text(self, text: str) -> None:
        """Guarda a primeira queixa que o cliente fez, enquanto não há protocolo, e o pedido citado."""
        if (order_id := extract_order_id(text)) is not None:
            self.last_order_id = order_id
        if (
            not self.complaint_registered
            and not self.complaint_summary
            and COMPLAINT_WORDS.search(fold(text))
        ):
            self.complaint_summary = redact(text.strip())[:SUMMARY_CHARS]

    def note_result(self, tool: str, ok: bool, code: str | None = None) -> None:
        """Conta falhas por (tool, código); um sucesso da tool zera as falhas dela."""
        if ok:
            self.failures = {k: n for k, n in self.failures.items() if k[0] != tool}
        else:
            key = (tool, code or "unknown")
            self.failures[key] = self.failures.get(key, 0) + 1

    def tripped(self, tool: str) -> bool:
        """O disjuntor da tool está aberto: algum código de erro dela já falhou demais."""
        return tool not in BREAKER_EXEMPT and any(
            n >= MAX_REPEATED_FAILURES for (t, _), n in self.failures.items() if t == tool
        )

    def reset_failures(self, tool: str) -> None:
        self.failures = {k: n for k, n in self.failures.items() if k[0] != tool}

    def absorb(self, tool: str, arguments: Any, result: dict[str, Any]) -> None:
        """Atualiza o estado com o resultado de uma tool (nunca com o que o modelo disse)."""
        ok = bool(result.get("ok"))
        error = result.get("error")
        self.note_result(tool, ok, error.get("code") if isinstance(error, dict) else None)
        if tool == "register_complaint" and isinstance(arguments, dict):
            if ok:
                self.pending_complaint = None
            elif isinstance(error, dict) and error.get("code") == "contact_needed":
                self.pending_complaint = {
                    "summary": arguments.get("summary"),
                    "order_id": arguments.get("order_id"),
                }
        data = result.get("data")
        if not ok or not isinstance(data, dict):
            return
        if tool in ("get_product", "calculate_payment_options") and isinstance(arguments, dict):
            asked = data.get("name") if tool == "get_product" else data.get("product")
            if (
                (arguments.get("name") or arguments.get("product_id"))
                and isinstance(asked, str)
                and asked not in self.asked_products
            ):
                self.asked_products.append(asked)
        if tool == "get_product":
            self._product(data)
        elif tool == "search_products":
            for item in data.get("items") or []:
                self._product(item)
        elif tool == "verify_customer" and isinstance(arguments, dict):
            order_id = arguments.get("order_id")
            if isinstance(order_id, int) and order_id not in self.verified_orders:
                self.verified_orders.append(order_id)
        elif tool == "register_complaint" and isinstance(data.get("ticket_id"), str):
            self.complaint_ticket = data["ticket_id"]

    def complaint_to_register(self, text: str) -> dict[str, Any] | None:
        """Os argumentos de ``register_complaint`` que o orquestrador deve usar se ``text`` traz contato.

        A reclamação que esperava contato, ou a queixa guardada (sem pedido verificado nem
        protocolo). Fala que só cita "pedido N" sem queixa é verificação, não reclamação.
        """
        if self.pending_complaint:
            return self.pending_complaint
        if not self.complaint_summary or self.verified_orders or self.complaint_registered:
            return None
        plain = fold(text)
        if _ORDER_REF.search(plain) and not COMPLAINT_WORDS.search(plain):
            return None
        return {"summary": self.complaint_summary}

    def verification_to_run(self, text: str) -> dict[str, Any] | None:
        """Os argumentos de ``verify_customer`` se a fala traz a prova e o pedido ainda não foi verificado.

        Só a prova da fala atual vale (nunca uma antiga em outro pedido: cada erro gasta o
        bloqueio). O pedido é o da fala ou, senão, o último citado (``last_order_id``).
        """
        proof = extract_proof(text)
        order_id = self.last_order_id
        if proof is None or order_id is None or order_id in self.verified_orders:
            return None
        return {"order_id": order_id, "proof": proof}

    def _lost_order_number(self, user_texts: Sequence[str]) -> bool:
        """Reclamação sem número de pedido, ainda sem pedido verificado nem protocolo."""
        if self.verified_orders or self.complaint_ticket or not user_texts:
            return False
        text = " ".join(fold(t) for t in user_texts)
        return bool(COMPLAINT_WORDS.search(text) and _NO_ORDER_NUMBER.search(text))

    def _product(self, item: Any) -> None:
        if not isinstance(item, dict):
            return
        pid, name, price = item.get("product_id"), item.get("name"), item.get("effective_price_brl")
        if isinstance(pid, int) and isinstance(name, str) and isinstance(price, str):
            self.products.pop(pid, None)  # reinsere no fim: o mais recente fica por último
            self.products[pid] = ProductNote(name, price)

    def _asked_order(self) -> str:
        numbered = [f"{i}º {name}" for i, name in enumerate(self.asked_products, start=1)]
        if len(numbered) > ASKED_SHOWN:
            numbered = [*numbered[:ASKED_HEAD], "...", *numbered[-ASKED_TAIL:]]
        return "; ".join(numbered)

    def summary_lines(self, user_texts: Sequence[str] = ()) -> list[str]:
        lines: list[str] = []
        if self.products:
            recent = list(self.products.items())[-SHOWN_PRODUCTS:]
            lines.append(
                "Produtos já consultados: "
                + "; ".join(f"{n.name} (id {pid}) {n.price}" for pid, n in recent)
            )
        if self.asked_products:
            lines.append("Produtos que o cliente perguntou, em ordem: " + self._asked_order())
        if self.verified_orders:
            lines.append(
                "Pedidos verificados nesta conversa: " + ", ".join(map(str, self.verified_orders))
            )
        if self.complaint_ticket:
            lines.append(f"Protocolo de reclamação já registrado: {self.complaint_ticket}")
        if self._lost_order_number(user_texts):
            lines.append(NO_ORDER_HINT)
        if self.offers_made:
            lines.append(
                "Ofertas já feitas ao cliente (não repita): " + ", ".join(self.offers_made)
            )
        return lines


def compose(text: str, working: WorkingState) -> tuple[str, int]:
    """Tira da resposta aprovada o gancho final que o cliente já recebeu; registra os novos.

    Só olha a última frase, e só se ela for uma pergunta que oferece algo conhecido. Nunca deixa
    a resposta vazia. Devolve o texto e quantas frases removeu (0 ou 1).
    """
    body = re.sub(r"[^\w?]+$", "", text)  # sem emoji nem espaço no fim
    if not body.endswith("?"):
        return text, 0
    boundaries = list(_BOUNDARY.finditer(body))
    cut = boundaries[-1] if boundaries else None
    last = body[cut.end() :] if cut else body
    labels = [label for label, pattern in OFFERS.items() if pattern.search(last)]
    if not labels:
        return text, 0
    if cut and cut.start() > 0 and all(label in working.offers_made for label in labels):
        return body[: cut.start()].rstrip(), 1
    working.offers_made.extend(label for label in labels if label not in working.offers_made)
    return text, 0
