"""Manual de políticas da loja, dividido por seção e consultado por tópico.

O texto das seções é literal, extraído do manual (``scripts/extract_policies.py``). As
contradições do manual são decididas uma vez em ``policies_notes.json``: cada seção devolvida
leva as notas do seu tópico, para o modelo nunca escolher entre duas versões.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Self

from emporio.data.repository import DataLoadError
from emporio.paths import default_data_dir

MAX_TOPICS: Final = 3
_SECTION: Final = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)


class Topic(StrEnum):
    EMPRESA = "empresa"
    HORARIO = "horario"
    PAGAMENTO = "pagamento"
    TROCAS = "trocas_devolucoes"
    FRETE = "frete_entrega"
    PROMOCOES = "promocoes"
    ATENDIMENTO = "atendimento"
    GARANTIA = "garantia"
    PRIVACIDADE = "privacidade"
    DISPOSICOES = "disposicoes"
    TODOS = "todos"  # o manual inteiro


SECTION_TOPIC: Final = {
    1: Topic.EMPRESA,
    2: Topic.HORARIO,
    3: Topic.PAGAMENTO,
    4: Topic.TROCAS,
    5: Topic.FRETE,
    6: Topic.PROMOCOES,
    7: Topic.ATENDIMENTO,
    8: Topic.GARANTIA,
    9: Topic.PRIVACIDADE,
    10: Topic.DISPOSICOES,
}

# Palavras que o cliente usa, para o modelo escolher o tópico certo.
TOPIC_HELP: Final = {
    Topic.EMPRESA: "quem somos, endereço, onde fica a loja, telefone, e-mail, CNPJ, missão",
    Topic.HORARIO: "horário de funcionamento, abre, fecha, sábado, domingo, feriado",
    Topic.PAGAMENTO: "formas de pagamento, PIX, cartão, boleto, parcelar, parcelas, juros",
    Topic.TROCAS: "devolver, devolução, trocar, troca, arrependimento, reembolso, defeito",
    Topic.FRETE: "frete, entrega, prazo, Correios, Jadlog, rastreamento, envio para outra cidade",
    Topic.PROMOCOES: "promoção, desconto, Black Friday, cumulativo, liquidação",
    Topic.ATENDIMENTO: "WhatsApp, atendimento, acessórios, reclamação, tempo de resposta",
    Topic.GARANTIA: "garantia legal, garantia do fabricante, o que não cobre",
    Topic.PRIVACIDADE: "LGPD, dados pessoais, privacidade, excluir meus dados",
    Topic.DISPOSICOES: "disposições finais, atualização do manual",
    Topic.TODOS: "pergunta que cruza vários assuntos ou não cabe em um tópico",
}


# Cobertura lexical: pergunta do cliente contra o manual inteiro. Regras, não semântica.
_WORD: Final = re.compile(r"[a-z0-9]+")
_STOP: Final = frozenset(
    """
    a o as os um uma uns umas de do da dos das em no na nos nas por para pra com sem sobre ate ao
    aos e ou mas que se como qual quais quanto quantos quantas quando onde quem porque porque
    eu voce voces vc vcs ele ela eles elas meu minha meus minhas seu sua seus suas nosso nossa
    me te lhe nos isso isto esse essa esses essas aquele aquela aqui ali la ja so tambem mais
    menos muito muita pouco bem mal sim nao oi ola bom boa dia tarde noite obrigado obrigada
    ser sou e eh foi era sao esta estao estou tem tenho temos tinha ter vai vou ir ha fazer faz
    pode podem posso podemos poderia queria quero quer gostaria gostei saber dizer diz falar
    fala informar informa ver veja olha preciso precisa precisar ajuda ajudar duvida pergunta
    algum alguma alguns algumas algo todo toda todos todas tipo coisa coisas isso tudo
    loja lojas empresa voces pessoal gente favor pois entao agora hoje ainda sempre nunca
    existe existem tipos aceitam vendem trabalham funciona fazem fazemos
    """.split()  # noqa: SIM905
)  # "aceitam"/"vendem"/"trabalham": verbos de pergunta; o substantivo é que decide a cobertura
# o cliente diz "cair"; o manual diz "queda". "ar-condicionado" perde o "ar" (curto) e o radical
# de "condicionado" colidiria com "condições".
_SYNONYMS: Final = {
    "cair": "queda",
    "cai": "queda",
    "caiu": "queda",
    "caindo": "queda",
    "condicionado": "arcondicionado",
}
_PROPER_NOUN: Final = re.compile(r"(?<=[\wÀ-ÿ,]\s)[A-ZÀ-Ý][\wÀ-ÿ]+")
_STEM: Final = 6  # truncar o radical junta plural, gênero e flexão verbal ("aceitam"/"aceitamos")

Coverage = Literal["covered", "partial", "none"]


def _unaccent(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _stem(word: str) -> str:
    for plural, singular in (("oes", "ao"), ("aes", "ao"), ("ais", "al"), ("eis", "el")):
        if word.endswith(plural) and len(word) > 4:
            word = word[: -len(plural)] + singular
            break
    else:
        if word.endswith("s") and len(word) > 3:
            word = word[:-1]
    return word[:_STEM]


def content_terms(text: str) -> list[tuple[str, str]]:
    """(palavra, radical) dos termos de conteúdo: sem stopwords, curtos nem números/códigos."""
    seen: dict[str, str] = {}
    for word in _WORD.findall(_unaccent(text)):
        if word in _STOP or len(word) < 3 or any(c.isdigit() for c in word):
            continue
        word = _SYNONYMS.get(word, word)
        seen.setdefault(word, _stem(word))
    return list(seen.items())


def proper_nouns(question: str) -> list[str]:
    """Palavras capitalizadas no meio da pergunta (cidades, marcas): nomes próprios."""
    return _PROPER_NOUN.findall(question)


@dataclass(frozen=True, slots=True)
class PolicyCoverage:
    level: Coverage
    unmatched: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicySection:
    number: int
    title: str
    topic: Topic
    text: str  # literal do manual
    notes: tuple[str, ...] = ()  # decisões do projeto sobre contradições do manual


class PolicyStore:
    def __init__(self, markdown: str, notes: Sequence[tuple[frozenset[Topic], str]] = ()) -> None:
        matches = list(_SECTION.finditer(markdown))
        if [int(m[1]) for m in matches] != list(SECTION_TOPIC):
            raise DataLoadError(f"o manual deve ter as seções 1 a {len(SECTION_TOPIC)} em ordem")
        ends = [m.start() for m in matches[1:]] + [len(markdown)]
        self.sections = tuple(
            PolicySection(
                number=int(m[1]),
                title=m[2],
                topic=SECTION_TOPIC[int(m[1])],
                text=markdown[m.end() : end].strip(),
                notes=tuple(note for topics, note in notes if SECTION_TOPIC[int(m[1])] in topics),
            )
            for m, end in zip(matches, ends, strict=True)
        )
        everything = " ".join(f"{s.title} {s.text} {' '.join(s.notes)}" for s in self.sections)
        self._vocabulary = frozenset(stem for _, stem in content_terms(everything))

    def coverage(self, question: str, ignore_terms: Iterable[str] = ()) -> PolicyCoverage:
        """Quanto da pergunta o manual inteiro (texto + notas) trata, por termos de conteúdo.

        ``covered``: todos os termos aparecem; ``partial``: alguns; ``none``: nenhum. Lexical:
        o termo no manual não prova que a pergunta foi respondida, só que o assunto existe.
        Nomes próprios da pergunta e ``ignore_terms`` (nomes do catálogo) não contam.
        """
        skip = {s for _, s in content_terms(" ".join([*ignore_terms, *proper_nouns(question)]))}
        terms = [(w, s) for w, s in content_terms(question) if s not in skip]
        unmatched = tuple(word for word, stem in terms if stem not in self._vocabulary)
        if not unmatched:
            return PolicyCoverage("covered")
        return PolicyCoverage("none" if len(unmatched) == len(terms) else "partial", unmatched)

    @classmethod
    def load(cls, directory: Path | None = None) -> Self:
        """Lê ``policies.md`` e ``policies_notes.json``, que ficam ao lado da pasta ``raw``."""
        directory = directory or default_data_dir().parent
        try:
            markdown = (directory / "policies.md").read_text(encoding="utf-8")
            decisions = json.loads((directory / "policies_notes.json").read_text(encoding="utf-8"))
            notes = [(frozenset(map(Topic, d["topics"])), d["note"]) for d in decisions]
        except (OSError, ValueError, KeyError) as exc:
            raise DataLoadError(f"políticas ilegíveis em {directory}: {exc}") from exc
        return cls(markdown, notes)

    def get(self, topics: Sequence[Topic]) -> list[PolicySection]:
        """Seções dos tópicos pedidos, na ordem do manual; ``todos`` devolve o manual inteiro."""
        if not 1 <= len(topics) <= MAX_TOPICS:
            raise ValueError(f"informe de 1 a {MAX_TOPICS} tópicos")
        if Topic.TODOS in topics:
            return list(self.sections)
        return [section for section in self.sections if section.topic in topics]
