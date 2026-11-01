# /// script
# requires-python = ">=3.12"
# dependencies = ["pdfplumber>=0.11.10,<0.12"]
# ///
"""Extrai o manual de políticas (PDF) para Markdown, com uma seção por capítulo.

Uso: uv run scripts/extract_policies.py data/raw/politicas_da_loja.pdf data/policies.md

O texto das seções é mantido como está no manual; só saem a capa, o cabeçalho e o rodapé de
cada página, e as quebras de linha do PDF são desfeitas. Revise o resultado antes de commitar.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pdfplumber

SECTION_COUNT = 10
HEADER = "Empório da Música Manual de Políticas e Procedimentos"
FOOTER = re.compile(r"^Página \d+$")
SUBSECTION = re.compile(r"^\d+\.\d+ \S")
NUMBERED = re.compile(r"^(\d+)\. (\S.*)$")
LINE_GAP_MAX = 5.5  # espaço entre linhas do mesmo parágrafo; entre parágrafos é bem maior

Line = tuple[str, float | None]  # texto e distância da linha de cima (None: sem linha acima)


def table_markdown(rows: list[list[str | None]]) -> str:
    cells = [[" ".join((cell or "").split()) for cell in row] for row in rows]
    lines = ["| " + " | ".join(cells[0]) + " |", "|" + " --- |" * len(cells[0])]
    lines += ["| " + " | ".join(row) + " |" for row in cells[1:]]
    return "\n".join(lines)


def page_lines(page: pdfplumber.page.Page) -> list[Line]:
    """Linhas de texto da página, com cada tabela no lugar dela como uma linha só."""
    lines: list[Line] = []
    top = 0.0
    for table in sorted(page.find_tables(), key=lambda t: t.bbox[1]):
        lines += region_lines(page, top, table.bbox[1])
        lines.append((table_markdown(table.extract()), None))
        top = table.bbox[3]
    return lines + region_lines(page, top, page.height)


def region_lines(page: pdfplumber.page.Page, top: float, bottom: float) -> list[Line]:
    if bottom - top < 1:
        return []
    lines: list[Line] = []
    above: float | None = None  # base da última linha mantida
    for line in page.within_bbox((0, top, page.width, bottom)).extract_text_lines():
        text = line["text"]
        if text == HEADER or FOOTER.match(text):
            continue
        lines.append((text, None if above is None else line["top"] - above))
        above = line["bottom"]
    return lines


def to_markdown(lines: list[Line]) -> str:
    blocks: list[tuple[str, str]] = []  # (tipo, texto)
    expected = 1
    for line, gap in lines:
        number = NUMBERED.match(line)
        if line.startswith("|"):
            blocks.append(("table", line))
        elif number and int(number[1]) == expected:
            blocks.append(("heading", f"## {number[1]}. {number[2]}"))
            expected += 1
        elif SUBSECTION.match(line):
            blocks.append(("heading", f"### {line}"))
        elif line.startswith("• "):
            blocks.append(("item", "- " + line[2:]))
        elif number:
            blocks.append(("item", line))
        elif blocks and continues(blocks[-1][0], gap):
            kind, text = blocks[-1]
            blocks[-1] = (kind, f"{text} {line}")
        else:
            blocks.append(("paragraph", line))

    if expected != SECTION_COUNT + 1:
        raise SystemExit(f"esperava {SECTION_COUNT} seções, encontrei {expected - 1}")

    out = [blocks[0][1]]
    for (previous, _), (kind, text) in zip(blocks, blocks[1:], strict=False):
        out.append(("\n" if previous == kind == "item" else "\n\n") + text)
    return "".join(out) + "\n"


def continues(previous_kind: str, gap: float | None) -> bool:
    """A linha segue o bloco anterior (quebra de linha do PDF) em vez de abrir um parágrafo."""
    return previous_kind not in ("heading", "table") and (gap is None or gap <= LINE_GAP_MAX)


def main() -> None:
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    with pdfplumber.open(source) as pdf:
        lines = [line for page in pdf.pages[1:] for line in page_lines(page)]  # a capa fica de fora
    target.write_text(to_markdown(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
