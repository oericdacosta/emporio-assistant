from __future__ import annotations

from pathlib import Path

import pytest

from emporio.data.policies import (
    SECTION_TOPIC,
    TOPIC_HELP,
    PolicyStore,
    Topic,
)
from emporio.data.repository import DataLoadError


@pytest.fixture(scope="module")
def store() -> PolicyStore:
    return PolicyStore.load(Path(__file__).resolve().parents[3] / "data")


def test_manual_has_ten_numbered_sections_in_order(store: PolicyStore) -> None:
    assert [s.number for s in store.sections] == list(range(1, 11))
    assert store.sections[0].title == "Sobre a Empório da Música"
    assert store.sections[9].title == "Disposições Finais"


@pytest.mark.parametrize(("number", "topic"), SECTION_TOPIC.items())
def test_each_topic_returns_its_section(store: PolicyStore, number: int, topic: Topic) -> None:
    assert [s.number for s in store.get([topic])] == [number]


def test_returns_section_is_complete_and_literal(store: PolicyStore) -> None:
    (section,) = store.get([Topic.TROCAS])
    assert section.number == 4
    assert "7 (sete) dias corridos após o recebimento" in section.text
    assert "30 (trinta) dias corridos após a compra" in section.text
    assert "Boquilhas de instrumentos de sopro" in section.text


def test_tables_keep_their_rows(store: PolicyStore) -> None:
    (payment,) = store.get([Topic.PAGAMENTO])
    assert "| PIX | Pagamento à vista | 5% de desconto sobre o preço de tabela |" in payment.text
    assert "| Cartão de Crédito | Até 12x sem juros | Parcela mínima de R$ 100,00 |" in payment.text
    (shipping,) = store.get([Topic.FRETE])
    assert "| PAC (Correios) | 5 a 12 dias úteis | Sim | Incluído |" in shipping.text
    assert "| Jadlog (.package) | 3 a 8 dias úteis | Sim | Incluído |" in shipping.text
    (hours,) = store.get([Topic.HORARIO])
    assert "| Sábado | 09:00 às 13:00 |" in hours.text


def test_page_headers_and_footers_are_gone(store: PolicyStore) -> None:
    text = "\n".join(s.text for s in store.sections)
    assert "Manual de Políticas e Procedimentos" not in text
    assert "Página " not in text


def test_several_topics_come_in_manual_order(store: PolicyStore) -> None:
    sections = store.get([Topic.FRETE, Topic.TROCAS, Topic.TROCAS])
    assert [s.number for s in sections] == [4, 5]


def test_all_topic_returns_the_whole_manual(store: PolicyStore) -> None:
    assert len(store.get([Topic.TODOS])) == 10


@pytest.mark.parametrize(
    "topics", [[], [Topic.EMPRESA, Topic.HORARIO, Topic.FRETE, Topic.GARANTIA]]
)
def test_topic_count_is_bounded(store: PolicyStore, topics: list[Topic]) -> None:
    with pytest.raises(ValueError, match="1 a 3"):
        store.get(topics)


def test_decisions_travel_with_the_sections_of_their_topics(store: PolicyStore) -> None:
    def notes(topic: Topic) -> str:
        return " ".join(store.get([topic])[0].notes)

    assert "(67) 3341-4444" in notes(Topic.EMPRESA) and "(67) 3321-4500" in notes(Topic.EMPRESA)
    assert "(67) 3321-4500" in notes(Topic.ATENDIMENTO)
    assert "contato@emporiodamusica.com.br" in notes(Topic.EMPRESA)
    assert "R$ 50,00 até 3x" in notes(Topic.PAGAMENTO)
    assert "65 itens" in notes(Topic.EMPRESA)
    assert "30 dias" in notes(Topic.TROCAS) and "90 dias" in notes(Topic.GARANTIA)
    assert store.get([Topic.HORARIO])[0].notes == ()


def test_every_topic_has_help_keywords() -> None:
    assert set(TOPIC_HELP) == set(Topic)
    assert "devolver" in TOPIC_HELP[Topic.TROCAS]
    assert "parcelar" in TOPIC_HELP[Topic.PAGAMENTO]


def test_topic_names_are_the_ones_the_model_chooses_from() -> None:
    assert [t.value for t in Topic] == [
        "empresa",
        "horario",
        "pagamento",
        "trocas_devolucoes",
        "frete_entrega",
        "promocoes",
        "atendimento",
        "garantia",
        "privacidade",
        "disposicoes",
        "todos",
    ]


def test_manual_with_a_missing_section_is_rejected() -> None:
    with pytest.raises(DataLoadError, match="seções 1 a 10"):
        PolicyStore("## 1. Sobre\n\ntexto\n\n## 3. Pagamento\n\ntexto\n")


def test_missing_files_are_reported(tmp_path: Path) -> None:
    with pytest.raises(DataLoadError, match="ilegíveis"):
        PolicyStore.load(tmp_path)


def test_unknown_topic_in_the_notes_is_reported(tmp_path: Path) -> None:
    sections = "".join(f"## {n}. Seção {n}\n\ntexto\n\n" for n in range(1, 11))
    (tmp_path / "policies.md").write_text(sections, encoding="utf-8")
    (tmp_path / "policies_notes.json").write_text('[{"topics": ["xyz"], "note": "n"}]')
    with pytest.raises(DataLoadError):
        PolicyStore.load(tmp_path)


def test_default_location_is_next_to_the_raw_folder() -> None:
    assert len(PolicyStore.load().sections) == 10
