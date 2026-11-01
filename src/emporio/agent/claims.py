"""Portão "sem tool, sem fato": o rascunho afirma fatos da loja sem ter consultado nada?

O orquestrador (não o prompt) decide quando uma resposta precisa de evidência: se nenhuma tool
rodou no turno, a resposta é longa o bastante e não é só pergunta, um ``FactClaimDetector`` diz se
ela afirma fatos da loja. O detector padrão faz uma chamada mínima ao MESMO ``LLMClient`` (sem
tools, temperatura 0, 4 tokens). Qualquer falha do detector conta como "não afirma" (o portão
nunca derruba o turno).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from emporio.domain.complaint import fold
from emporio.domain.store import WHATSAPP
from emporio.llm.base import LLMClient, Message, Usage

log = logging.getLogger(__name__)

MIN_CHARS: Final = 40
RECENT_ANSWERS: Final = 3

DETECTOR_PROMPT: Final = (
    "A resposta afirma fatos sobre a loja (políticas, instalações, serviços, horários, preços, "
    "prazos) que só uma consulta poderia confirmar? Responda SIM ou NÃO. Responda NÃO se: só "
    "repete algo já dito na conversa; cumprimenta, recusa ou pergunta; PEDE dados ao cliente "
    "(e-mail, telefone, número do pedido, contato); ou só afirma fatos fixos do atendimento: a "
    "loja vende somente instrumentos musicais (não vende acessórios como cordas, cabos, "
    "palhetas, pedais ou amplificadores) e o assistente não concede desconto, não fecha pedido "
    "nem recebe pagamento. Os fatos já confirmados nesta conversa (válidos) são fonte válida: "
    "repeti-los não é afirmar fato novo."
)
NO_EVIDENCE_CORRECTION: Final = (
    "Você afirmou fatos da loja sem consultar nenhuma ferramenta. Consulte a ferramenta "
    "adequada ou diga que vai confirmar com a equipe; se a pergunta é sobre a própria conversa, "
    "responda só com o que a conversa já confirma."
)
SERVICE_CORRECTION: Final = (
    "Você afirmou ou negou um serviço ou instalação da loja (conserto, aula, estacionamento, "
    "ar-condicionado, animais...) sem consultar nada. O manual não cobre isso: diga que o manual "
    "não traz essa informação e que vai confirmar com a equipe."
)
NO_EVIDENCE_MESSAGE: Final = (
    "Essa informação eu não tenho confirmada aqui e prefiro não te passar nada errado. "
    f"Pode me chamar no WhatsApp {WHATSAPP} que a equipe confirma pra você."
)
_SENTENCE_END: Final = re.compile(r"[.!?\n]+")
# Frases que o detector (um juiz de 4 tokens) julga de modo instável e que não precisam dele:
# pedir dado ao cliente e os fatos fixos do atendimento (a loja só vende instrumentos; sem
# desconto). Deterministas: não dependem do humor do modelo.
_BENIGN: Final = re.compile(
    r"(?:"
    r"(?:me\s+(?:passa|informe|diga|envie)|pode\s+me\s+(?:passar|informar|dizer)|"
    r"preciso\s+(?:que\s+voc[eê]\s+(?:me\s+)?(?:informe|passe|diga)|do\s+(?:seu\s+)?(?:e-?mail|n[uú]mero|telefone)))"
    r"|(?:s[oó]\s+(?:trabalh\w+\s+com|vend\w+)|trabalh\w+\s+s[oó]\s+com)\s+instrumentos"
    r"|n[aã]o\s+(?:vendemos|trabalhamos\s+com)\s+(?:cordas|cabos|palhetas|pedais|acess[oó]rios)"
    r"|n[aã]o\s+(?:consigo|posso)\s+(?:conceder\s+)?desconto"
    r")",
    re.IGNORECASE,
)


# Afirmação (ou negação) sobre serviço ou instalação da loja: o modelo inventa as negativas
# ("não fazemos conserto") com a mesma segurança das positivas. Sobre texto dobrado. Os fatos
# fixos do atendimento (sem acessório, sem desconto) ficam de fora pelo objeto e pelo ``_BENIGN``.
_SERVICE_CLAIM: Final = re.compile(
    r"\bnao\s+(?:fazemos|faz|damos|da|temos|tem|oferecemos|oferece|aceitamos|aceita"
    r"|possuimos|entregamos)\b(?!\s+(?:problema|sentido|nada|diferenca|de que|mal"
    r"|desconto|acessorios?|cordas?|cabos?|palhetas?|pedais|amplificadores)\b)"
    r"|\bnao\s+(?:faco|dou)\s+(?:conserto|aula|manutencao|reparo|servico)"
    r"|\b(?:fazemos|damos|oferecemos|temos|tem|aceitamos|aceita|da|faz)\s+"
    r"(?:conserto|aula|estacionamento|wi-?fi|cafe|banheiro|ar-?condicionado|animais|pets"
    r"|garantia estendida|nota fiscal)"
)


@dataclass(frozen=True, slots=True)
class Detection:
    claims_facts: bool
    usage: Usage = Usage()  # noqa: RUF009


class FactClaimDetector(Protocol):
    def detect(
        self, recent_answers: Sequence[str], draft: str, known_facts: Sequence[str] = ()
    ) -> Detection: ...


# oferta, pergunta ou saudação: a frase não afirma nada sobre a loja
_OFFER: Final = re.compile(
    r"^\W*(?:quer|posso|pode (?:me|te)|se quiser|gostaria|prefere|qual|como|me passa|oi|ola)\b"
)
# verbo de afirmação sobre a loja; sobrou depois do trecho benigno = o rascunho diz mais
_STORE_VERB: Final = re.compile(
    r"\b(?:temos|tem|fazemos|damos|oferecemos|aceitamos|aceita|possui|dispomos|entregamos"
    r"|emitimos)\b"
)
_STORE_VERB_1ST: Final = re.compile(
    r"\b(?:temos|fazemos|damos|oferecemos|aceitamos|dispomos|entregamos|emitimos|vendemos)\b"
)
# pergunta sobre a própria conversa: a fonte é a conversa, não uma tool
_ABOUT_CONVERSATION: Final = re.compile(
    r"\b(?:falei|perguntei|disse|mencionei|comentei|primeir[oa]|segund[oa]|terceir[oa]"
    r"|de novo|anterior|mesmo que)\b"
)


def about_conversation(text: str) -> bool:
    return _ABOUT_CONVERSATION.search(fold(text)) is not None


def needs_check(draft: str) -> bool:
    """Só o rascunho que afirma algo além de pedir dado, ofertar ou dizer fatos fixos vai ao detector.

    Pula: rascunho curto; pergunta única (sem verbo de afirmação na 1ª pessoa); ou sentenças que
    são todas oferta/pergunta/saudação ou frase benigna sem outro verbo de afirmação sobre a loja.
    """
    if len(draft) < MIN_CHARS:
        return False
    plain = fold(draft)
    sentences = [s.strip() for s in _SENTENCE_END.split(plain) if s.strip()]
    if draft.rstrip().endswith("?") and len(sentences) <= 1 and not _STORE_VERB_1ST.search(plain):
        return False
    claims = [s for s in sentences if not _OFFER.match(s)]
    return not all(_BENIGN.search(s) and not _STORE_VERB.search(_BENIGN.sub("", s)) for s in claims)


def service_claim(draft: str) -> bool:
    """O rascunho afirma ou nega serviço ou instalação da loja (sentença que não é fato fixo)?"""
    for sentence in _SENTENCE_END.split(fold(draft)):
        if _SERVICE_CLAIM.search(sentence) and not (
            _BENIGN.search(sentence) and not _SERVICE_CLAIM.search(_BENIGN.sub("", sentence))
        ):
            return True
    return False


class LLMFactClaimDetector:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def detect(
        self, recent_answers: Sequence[str], draft: str, known_facts: Sequence[str] = ()
    ) -> Detection:
        recent = "\n---\n".join(recent_answers[-RECENT_ANSWERS:]) or "(nenhuma)"
        known = "\n".join(known_facts) or "(nenhum)"
        try:
            response = self._llm.chat(
                [
                    Message("system", DETECTOR_PROMPT),
                    Message(
                        "user",
                        f"Últimas mensagens do assistente:\n{recent}\n\n"
                        f"Fatos já confirmados nesta conversa (válidos):\n{known}\n\n"
                        f"Rascunho:\n{draft}",
                    ),
                ],
                (),
                tool_choice="none",
                temperature=0.0,
                max_tokens=4,
            )
        except Exception:
            log.warning("detector de afirmações falhou; seguindo sem o portão", exc_info=True)
            return Detection(False)
        verdict = response.message.content.strip().upper().replace("Ã", "A")
        return Detection(verdict.startswith("SIM"), response.usage)
