"""Roteador de escopo: responde sem o modelo o que é injeção, fora de escopo ou emergência.

Roda antes do modelo, só com regras determinísticas sobre a fala do cliente (sem acento, em
minúsculas). Resposta pronta é mais barata, mais rápida e não depende do modelo obedecer ao prompt.

Limite: são REGRAS, não um classificador. Os padrões são conservadores de propósito (melhor deixar
passar do que barrar um cliente de verdade) e o modelo, com o prompt e os filtros de saída, continua
sendo a rede de segurança para o que escapar. Regra de ouro: ``out_of_scope`` nunca age numa fala
que cita algo da loja (produto, pedido, preço...), e ``injection`` e ``emergency`` agem sempre
(``socorro`` isolado só sem assunto da loja). Só olha a fala atual, não o histórico.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from emporio.agent.types import Route


@dataclass(frozen=True, slots=True)
class Routed:
    route: Route
    reply: str


INJECTION_REPLY: Final = (
    "Isso eu não consigo fazer, mas posso ajudar com instrumentos, preços, pedidos e as regras "
    "da loja. O que você está procurando? 🎸"
)
OUT_OF_SCOPE_REPLY: Final = (
    "Esse assunto foge do que eu faço por aqui: sou o assistente da Empório da Música e cuido de "
    "instrumentos, preços, pedidos e regras da loja. Posso ajudar com algo disso?"
)
EMERGENCY_REPLY: Final = (
    "Isso parece uma emergência e o mais importante agora é a sua saúde. Procure atendimento de "
    "urgência já: ligue para o SAMU no 192 ou vá ao pronto-atendimento mais próximo. "
    "Quando estiver bem, é só voltar aqui."
)


def _norm(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in plain if not unicodedata.combining(c))


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns))


_LANGS = r"(?:python|javascript|typescript|sql|java|html|css|c\+\+|php|golang)"
_INJECTION = _rx(
    r"\b(?:ignor|esquec|desconsider|descart)\w*\s+(?:todas?\s+)?(?:as\s+|suas\s+|tuas\s+|essas\s+|"
    r"aquelas\s+|todas\s+as\s+)?(?:instrucoes|regras|ordens|orientacoes|diretrizes)",
    r"\b(?:mostr|repit|revel|exib|imprim|vaz|copi|cole|me\s+de|me\s+passe)\w*\s+(?:o\s+|seu\s+|"
    r"teu\s+|todo\s+o\s+)*(?:system\s+)?prompt\b",
    r"\bsystem\s+prompt\b|\bprompt\s+d[eo]\s+sistema\b|\binstrucoes\s+(?:internas|iniciais)\b",
    r"^\s*system\s*:",
    r"\bvoce\s+agora\s+e\s+(?:um|uma|o|a|outro|outra)\b|\ba\s+partir\s+de\s+agora\s+voce\s+"
    r"(?:e|sera|vai\s+ser)\b",
    r"\bmodo\s+dan\b|\bdan\s+mode\b|\bdo\s+anything\s+now\b",
    r"\bresponda\s+(?:somente|apenas|so)\s+com\s+(?:um\s+)?json\b",
    r"\bliste\w*\s+(?:todas?\s+)?(?:as\s+)?(?:suas\s+|tuas\s+)?(?:ferramentas|tools)\b",
    r"\bquais\s+(?:sao\s+)?(?:as\s+)?(?:suas|tuas)\s+(?:ferramentas|tools)\b",
    r"\bdecod\w*.{0,60}\b(?:obedec|execut|siga|cumpr)\w*",
    r"\bbase64\b.{0,60}\b(?:obedec|execut)\w*",
)
_OUT_OF_SCOPE = _rx(
    r"\b(?:escrev|cri|fa[cz]|ger|program)\w*\s+(?:para\s+mim\s+|pra\s+mim\s+)?(?:um\s+|uma\s+|o\s+)?"
    r"(?:codigo|script|algoritmo)\b",
    rf"\b(?:escrev|cri|fa[cz]|resolv|corrij|program|implement)\w*\b.{{0,30}}\b{_LANGS}\b",
    r"\blinguagem\s+de\s+programacao\b",
    r"\breceita\s+d[eoa]s?\s+\w+",
    r"\bcomo\s+(?:se\s+)?(?:fazer|faz|preparar|prepara)\s+(?:um\s+|uma\s+)?"
    r"(?:bolo|pizza|lasanha|brigadeiro|feijoada|pao|macarrao|churrasco)\b",
    r"\beleic(?:ao|oes)\b|\bem\s+quem\s+(?:eu\s+)?(?:devo\s+)?vot\w+|\bpartido\s+politico\b|"
    r"\bbolsonaro\b|\blula\b",
    r"\btraduz\w*",
    r"\b(?:escrev|redij|fa[cz])\w*\s+(?:um\s+|uma\s+|o\s+)?(?:redacao|poema|poesia|ensaio|tcc|"
    r"curriculo|artigo\s+cientifico|trabalho\s+(?:escolar|da\s+escola|de\s+faculdade))\b",
)
_STORE_WORDS = _rx(
    r"violao|violoes|guitarra|baixo|bateria|teclado|ukulele|cavaco|flauta|saxofone|instrument",
    r"loja|preco|valor|pedido|compra|comprar|frete|entrega|rastre|garantia|troca|devol|pagamento",
    r"pix|parcel|cartao|produto|estoque|promo|desconto|atendimento|whatsapp|reclama|nota|musica",
)
_EMERGENCY = _rx(
    r"\bdor\s+(?:no|do)\s+peito\b",
    r"\bfalta\s+de\s+ar\b(?!\s+condicionado)",
    r"\binfarto\b|\bambulancia\b|\bsamu\b",
)
_SOS = re.compile(r"\bsocorro\b")


def route(text: str) -> Routed | None:
    """A resposta pronta, se a fala cai numa das classes; ``None`` segue para o modelo."""
    low = _norm(text)
    has_store = _STORE_WORDS.search(low) is not None
    if _EMERGENCY.search(low) or (_SOS.search(low) and not has_store):
        return Routed("emergency", EMERGENCY_REPLY)
    if _INJECTION.search(low):
        return Routed("injection", INJECTION_REPLY)
    if not has_store and _OUT_OF_SCOPE.search(low):
        return Routed("out_of_scope", OUT_OF_SCOPE_REPLY)
    return None
