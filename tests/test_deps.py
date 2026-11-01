"""O LangGraph é dependência de runtime: a importação tem de funcionar no CI."""

from __future__ import annotations

import langgraph.graph


def test_langgraph_imports() -> None:
    assert langgraph.graph.StateGraph
