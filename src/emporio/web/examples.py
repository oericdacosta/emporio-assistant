"""Conversas de exemplo gravadas (``examples/conversations/*.json``) para a aba Exemplos."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples" / "conversations"


def load_examples(directory: Path | None = None) -> list[dict[str, Any]]:
    """Os exemplos em ordem de nome; arquivo ilegível é ignorado."""
    items: list[dict[str, Any]] = []
    for path in sorted((directory or EXAMPLES_DIR).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("turns"), list):
            items.append(data)
    return items


def _md(text: str) -> str:
    return text.replace("$", "\\$")  # "R$ 10 ... R$ 20" viraria fórmula


def examples_tab() -> None:
    items = load_examples()
    if not items:
        st.info("No example conversations in examples/conversations.")
        return
    st.caption("Conversations recorded with the real model. To really chat, use the Chat tab.")
    for item in items:
        number, title = item.get("number"), item.get("title_en") or item["title"]
        with st.expander(f"Example {number} — {title}" if number else title):
            scenario = item.get("scenario_en") or item["note"]
            st.caption(f"{scenario} Simulated clock: {item['now']}. Model: {item['model']}.")
            for turn in item["turns"]:
                with st.chat_message("user"):
                    st.markdown(_md(turn["user"]))
                with st.chat_message("assistant"):
                    st.markdown(_md(turn["assistant"]))
                    if turn["tools"]:
                        for tool in turn["tools"]:
                            st.caption(
                                f"query: {tool['name']} "
                                f"{json.dumps(tool['arguments'], ensure_ascii=False)}"
                            )
                    else:
                        st.caption("no queries")
