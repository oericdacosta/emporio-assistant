from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emporio.data.repository import CsvRepository
from emporio.data.search import looks_like_accessory, normalize, rank_products, search_products


@pytest.fixture(scope="module")
def repo() -> CsvRepository:
    return CsvRepository.load(Path(__file__).resolve().parents[3] / "data" / "raw")


def ids(hits: object) -> list[int]:
    return [h.product.id for h in hits]  # type: ignore[attr-defined]


@pytest.mark.parametrize("query", ["gd20", "GD-20", "takamine gd 20", "takamini gd20"])
def test_model_name_finds_the_product_first(repo: CsvRepository, query: str) -> None:
    assert search_products(repo, query).hits[0].product.id == 95


def test_brand_alone_is_ambiguous(repo: CsvRepository) -> None:
    result = search_products(repo, "takamine")
    assert sorted(ids(result.candidates)) == [95, 100, 105, 115]


def test_a_specific_model_has_no_candidates(repo: CsvRepository) -> None:
    assert search_products(repo, "takamine gd 20").candidates == ()


def test_broad_listing_is_not_a_disambiguation(repo: CsvRepository) -> None:
    assert search_products(repo, "violão").candidates == ()


def test_guitars_under_1000_use_the_current_price_and_skip_unavailable(
    repo: CsvRepository,
) -> None:
    hits = rank_products(repo, category="violão", max_price=1000)
    assert ids(hits) == [83, 85, 89, 88, 81, 84, 87, 86, 107, 82, 91, 112]
    assert 96 not in ids(hits) and 113 not in ids(hits)


def test_price_range_is_inclusive_and_applies_to_the_promotional_price(
    repo: CsvRepository,
) -> None:
    # Ohana CK-20 custa 549 na tabela e 439,20 com a promoção ativa
    assert 121 in ids(rank_products(repo, category="ukulele", max_price=Decimal("439.20")))
    assert 121 not in ids(rank_products(repo, category="ukulele", max_price=Decimal("439.19")))
    assert 83 in ids(rank_products(repo, category="violão", min_price=Decimal("429.90")))


def test_unavailable_products_only_appear_when_asked(repo: CsvRepository) -> None:
    assert 96 not in ids(search_products(repo, "Giannini GF-3D").hits)
    assert ids(search_products(repo, "Giannini GF-3D", only_available=False).hits)[:1] == [96]


def test_search_finds_by_category_alias_and_specs(repo: CsvRepository) -> None:
    top = search_products(repo, "violão nylon yamaha").hits[0].product
    assert "Yamaha" in top.name and top.specs["strings"] == "nylon"
    assert search_products(repo, "uke kala").hits[0].product.name.startswith("Kala")


def test_seven_string_guitars_are_found(repo: CsvRepository) -> None:
    result = search_products(repo, "7 cordas", category="violão", limit=8)
    assert set(ids(result.hits)) <= {109, 110, 111, 112}
    assert 112 in ids(result.hits)


def test_description_is_not_indexed(repo: CsvRepository) -> None:
    # a descrição do 135 fala em Fender Jazz Bass, mas o produto é Music Man
    assert 135 not in ids(search_products(repo, "fender jazz", only_available=False).hits)


@pytest.mark.parametrize("category", ["sopro", "metais", "cordas orquestrais"])
def test_empty_category_returns_nothing_without_error(repo: CsvRepository, category: str) -> None:
    result = search_products(repo, category=category)
    assert result.hits == () and result.total_matches == 0
    assert not result.unknown_category


def test_unknown_category_is_flagged(repo: CsvRepository) -> None:
    result = search_products(repo, category="pedais")
    assert result.unknown_category and result.hits == ()


@pytest.mark.parametrize("query", ["flauta", "saxofone", "trompete"])
def test_unrelated_query_returns_nothing(repo: CsvRepository, query: str) -> None:
    assert search_products(repo, query).total_matches == 0


def test_limit_is_capped_and_total_matches_is_complete(repo: CsvRepository) -> None:
    result = search_products(repo, category="violão", limit=50)
    assert len(result.hits) == 8 and result.total_matches > 8
    assert len(search_products(repo, category="violão").hits) == 5


def test_sort_by_price(repo: CsvRepository) -> None:
    prices = [h.price.price for h in rank_products(repo, category="baixo", sort="price_desc")]
    assert len(prices) > 1 and prices == sorted(prices, reverse=True)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tem corda de violão aço?", True),
        ("quero uma palheta", True),
        ("cabo P10", True),
        ("pedal de distorção", True),
        ("violão 7 cordas", False),
        ("baixo 5 cordas", False),
        ("violão Yamaha C40", False),
        ("caixa de som", False),
    ],
)
def test_accessory_detector(text: str, expected: bool) -> None:
    assert looks_like_accessory(text) is expected


def test_normalize_strips_accents_and_splits_letters_from_digits() -> None:
    assert normalize("Violão GD20-Nylon") == "violao gd 20 nylon"
