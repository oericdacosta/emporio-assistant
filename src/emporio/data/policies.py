"""Manual de políticas da loja, dividido por seção e consultado por tópico.

O texto das seções é literal, extraído do manual (``scripts/extract_policies.py``). As
contradições do manual são decididas uma vez em ``policies_notes.json``: cada seção devolvida
leva as notas do seu tópico, para o modelo nunca escolher entre duas versões.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Self

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
