"""Busca de produtos por nome, categoria e specs, só com a biblioteca padrão.

O nome do produto quase nunca diz a categoria ("Yamaha C40 Nylon Natural" não contém "violão"),
então o índice junta o nome (peso cheio) com categoria, apelidos e specs (peso menor). A
descrição fica de fora de propósito: alguns produtos têm descrição que contradiz o nome.

O score é a fração do IDF da consulta que o produto cobre. Palavra fora do vocabulário entra no
denominador com o IDF máximo, por isso uma consulta sem relação com o catálogo não devolve nada.
Filtros de categoria, preço e disponibilidade rodam antes do ranking.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from difflib import get_close_matches
from functools import lru_cache
from typing import Final, Literal

from emporio.data.repository import CsvRepository
from emporio.domain.availability import is_sellable
from emporio.domain.models import Product
from emporio.domain.pricing import Price, quote

MAX_LIMIT: Final = 8
MIN_SCORE: Final = 0.5
EXTRA_WEIGHT: Final = 0.6  # categoria, apelidos e specs valem menos que o nome
TYPO_CUTOFF: Final = 0.75
TIE_TOLERANCE: Final = 1e-6
UNAVAILABLE_LIMIT: Final = 3

SortOrder = Literal["relevance", "price_asc", "price_desc"]

# Os apelidos são nossos; as categorias vêm do CSV. Cada apelido aponta para uma palavra do nome
# da categoria, na forma normalizada ("violão" -> "violoes").
CATEGORY_ALIASES: Final = {
    "violao": "violoes",
    "guitarra": "guitarras",
    "baixo": "baixos",
    "contrabaixo": "baixos",
    "bateria": "baterias",
    "teclado": "teclados",
    "piano": "pianos",
    "uke": "ukuleles",
    "ukulele": "ukuleles",
}

_SPEC_WORDS: Final = {"steel": "aco"}
_STOPWORDS: Final = frozenset(
    {"a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "e", "com", "sem"}
    | {"para", "pra", "por", "em", "no", "na", "tem", "quero", "queria", "preciso"}
)

_ACCESSORY: Final = re.compile(
    r"\b(corda|cordas|palheta|palhetas|cabo|cabos|pedal|pedais|amplificador|capa|case)\b"
)
# "violão 7 cordas" e "baixo 5 cordas" descrevem o instrumento, não o acessório
_STRING_COUNT: Final = re.compile(r"\b\d+ cordas\b")


def normalize(text: str) -> str:
    """Minúsculas, sem acento, letra separada de dígito: ``"GD-20"`` e ``"gd20"`` -> ``"gd 20"``."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# Qualificadores subjetivos não são termos do catálogo: viram sinal ou saem da consulta. Só saem se
# a palavra não existir no catálogo (o nome "Rozini RC-104 Nylon Estudante" continua buscável).
_CHEAP: Final = re.compile(r"\b(?:barat[oa]s?|em conta|economic[oa]s?)\b")
_SUBJECTIVE: Final = frozenset(
    {"iniciante", "iniciantes", "bom", "boa", "bons", "boas", "otimo", "otima", "otimos"}
    | {"otimas", "melhor", "melhores", "simples", "basico", "basica", "bonito", "bonita"}
)


def looks_like_accessory(text: str) -> bool:
    """A loja só vende instrumentos: corda, palheta, cabo, pedal, amplificador e capa são acessórios.

    "Caixa" fica de fora porque também é peça de bateria e de som.
    """
    return bool(_ACCESSORY.search(_STRING_COUNT.sub(" ", normalize(text))))


def _tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t not in _STOPWORDS]


def _spec_tokens(specs: Mapping[str, str]) -> Iterable[str]:
    for key, value in specs.items():
        if key == "electronics":
            if value == "yes":
                yield from ("eletrico", "eletroacustico")
        else:
            yield from _tokens(_SPEC_WORDS.get(value, value))


@dataclass(frozen=True, slots=True)
class _Entry:
    product: Product
    name: frozenset[str]
    extra: frozenset[str]


@dataclass(frozen=True, slots=True)
class SearchHit:
    product: Product
    price: Price
    score: float


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: tuple[SearchHit, ...]  # no máximo ``limit`` itens
    total_matches: int
    candidates: tuple[SearchHit, ...] = ()  # empate no topo: o agente pergunta qual
    unknown_category: bool = False
    # indisponíveis que casam com a busca melhor do que qualquer item disponível
    unavailable: tuple[SearchHit, ...] = ()
    relaxed: tuple[str, ...] = ()  # termos da consulta descartados para achar algo
    sort: SortOrder = "relevance"  # a ordem aplicada (barato/em conta vira price_asc)


class _Index:
    def __init__(self, repo: CsvRepository) -> None:
        self.repo = repo
        category_words = {c.id: set(_tokens(c.name)) for c in repo.categories()}
        self.category_words = category_words
        entries = []
        for product in repo.products():
            words = category_words.get(product.category_id, set())
            aliases = {alias for alias, word in CATEGORY_ALIASES.items() if word in words}
            extra = words | aliases | set(_spec_tokens(product.specs))
            entries.append(_Entry(product, frozenset(_tokens(product.name)), frozenset(extra)))
        self.entries = entries

        document_frequency: dict[str, int] = {}
        for entry in entries:
            for word in entry.name | entry.extra:
                document_frequency[word] = document_frequency.get(word, 0) + 1
        total = max(len(entries), 1)
        self.idf = {w: math.log(1 + total / n) for w, n in document_frequency.items()}
        self.max_idf = math.log(1 + total)
        self.vocabulary = sorted(self.idf)

    def category_ids(self, text: str) -> frozenset[int]:
        wanted = {CATEGORY_ALIASES.get(t, t) for t in _tokens(text)}
        if not wanted:
            return frozenset()
        return frozenset(cid for cid, words in self.category_words.items() if wanted <= words)

    def query_tokens(self, query: str) -> list[str]:
        return list(dict.fromkeys(self._fix_typo(t) for t in _tokens(query)))

    def _fix_typo(self, token: str) -> str:
        if token in self.idf or len(token) < 4 or not token.isalpha():
            return token
        close = get_close_matches(token, self.vocabulary, n=1, cutoff=TYPO_CUTOFF)
        return close[0] if close else token

    def score(self, query: list[str], entry: _Entry) -> float:
        total = sum(self.idf.get(t, self.max_idf) for t in query)
        hit = sum(
            self.idf[t] * (1.0 if t in entry.name else EXTRA_WEIGHT)
            for t in query
            if t in entry.name or t in entry.extra
        )
        return hit / total


@lru_cache(maxsize=4)
def _index_for(repo: CsvRepository) -> _Index:
    return _Index(repo)


def rank_products(
    repo: CsvRepository,
    query: str = "",
    *,
    category: str | None = None,
    min_price: Decimal | int | None = None,
    max_price: Decimal | int | None = None,
    only_available: bool = True,
    sort: SortOrder = "relevance",
) -> list[SearchHit]:
    """Todos os produtos que casam, já ordenados. ``search_products`` limita o tamanho da lista."""
    index = _index_for(repo)
    words = index.query_tokens(query)
    categories = index.category_ids(category) if category is not None else None

    hits: list[SearchHit] = []
    for entry in index.entries:
        product = entry.product
        if categories is not None and product.category_id not in categories:
            continue
        if only_available and not is_sellable(product):
            continue
        price = quote(product, repo.promotions_for(product.id))
        # a faixa vale para o preço vigente (com promoção) e inclui os limites
        if (min_price is not None and price.price < min_price) or (
            max_price is not None and price.price > max_price
        ):
            continue
        score = index.score(words, entry) if words else 1.0
        if score >= MIN_SCORE:
            hits.append(SearchHit(product, price, score))

    match sort:
        case "price_desc":
            hits.sort(key=lambda h: (-h.price.price, h.product.id))
        case "price_asc":
            hits.sort(key=lambda h: (h.price.price, h.product.id))
        case "relevance":
            hits.sort(key=lambda h: (-round(h.score, 6), h.price.price, h.product.id))
    return hits


def _strip_qualifiers(index: _Index, query: str) -> tuple[str, bool]:
    """A consulta sem os qualificadores subjetivos e se algum pedia preço baixo."""
    text, cheap = normalize(query), False

    def drop_cheap(match: re.Match[str]) -> str:
        nonlocal cheap
        if any(w in index.idf for w in match[0].split()):
            return match[0]
        cheap = True
        return " "

    text = _CHEAP.sub(drop_cheap, text)
    words = [w for w in text.split() if w not in _SUBJECTIVE or w in index.idf]
    return " ".join(words), cheap


def _relaxations(
    index: _Index, query: str, category: str | None
) -> Iterable[tuple[str, str | None, list[str]]]:
    """Consultas cada vez mais frouxas, só dentro da categoria já resolvida.

    1) sem as palavras que não existem no catálogo; 2) só a categoria. A terceira coluna é o que
    cada passo descarta, acumulado."""
    words = index.query_tokens(query)
    unknown = [w for w in words if w not in index.idf]
    known = [w for w in words if w in index.idf]
    if unknown and known:
        yield " ".join(known), category, unknown
    cat_word = next((w for w in known if index.category_ids(w)), None)
    resolved = category if category is not None else cat_word
    rest = [w for w in known if w != cat_word or category is not None]
    if resolved is not None and (unknown or rest):
        yield "", resolved, unknown + rest


def search_products(
    repo: CsvRepository,
    query: str = "",
    *,
    category: str | None = None,
    min_price: Decimal | int | None = None,
    max_price: Decimal | int | None = None,
    only_available: bool = True,
    sort: SortOrder = "relevance",
    limit: int = 5,
) -> SearchResult:
    index = _index_for(repo)
    query, cheap = _strip_qualifiers(index, query)
    if cheap and sort == "relevance":
        sort = "price_asc"

    def rank(q: str, cat: str | None) -> list[SearchHit]:
        return rank_products(
            repo,
            q,
            category=cat,
            min_price=min_price,
            max_price=max_price,
            only_available=only_available,
            sort=sort,
        )

    unknown = category is not None and not index.category_ids(category)
    ranked = rank(query, category)
    relaxed: tuple[str, ...] = ()
    if not ranked and not unknown:
        for relaxed_query, relaxed_category, dropped in _relaxations(index, query, category):
            if ranked := rank(relaxed_query, relaxed_category):
                query, category, relaxed = relaxed_query, relaxed_category, tuple(dropped)
                break
    return SearchResult(
        hits=tuple(ranked[: max(1, min(limit, MAX_LIMIT))]),
        total_matches=len(ranked),
        candidates=_tied_candidates(ranked) if _tokens(query) and sort == "relevance" else (),
        unknown_category=unknown,
        relaxed=relaxed,
        sort=sort,
        unavailable=_better_but_unavailable(repo, query, ranked, category, min_price, max_price)
        if only_available
        else (),
    )


def _better_but_unavailable(
    repo: CsvRepository,
    query: str,
    available: list[SearchHit],
    category: str | None,
    min_price: Decimal | int | None,
    max_price: Decimal | int | None,
) -> tuple[SearchHit, ...]:
    """Produtos fora de venda que casam com o texto melhor do que tudo que está à venda.

    Sem isso, perguntar por um modelo esgotado parece perguntar por um modelo que não existe."""
    if not _tokens(query):
        return ()
    best = available[0].score if available else 0.0
    everything = rank_products(
        repo,
        query,
        category=category,
        min_price=min_price,
        max_price=max_price,
        only_available=False,
    )
    better = [
        h for h in everything if not is_sellable(h.product) and h.score > best + TIE_TOLERANCE
    ]
    return tuple(better[:UNAVAILABLE_LIMIT])


def _tied_candidates(ranked: list[SearchHit]) -> tuple[SearchHit, ...]:
    """Empate no topo que cabe numa pergunta ("qual Takamine?"); lista grande não é ambiguidade."""
    if not ranked:
        return ()
    tied = tuple(h for h in ranked if ranked[0].score - h.score <= TIE_TOLERANCE)
    return tied if 2 <= len(tied) <= MAX_LIMIT else ()
