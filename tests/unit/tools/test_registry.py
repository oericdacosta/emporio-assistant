from __future__ import annotations

from typing import Any

from emporio.tools.deps import ToolContext
from emporio.tools.registry import Params, Tool, ToolError, ToolRegistry


class Args(Params):
    value: int


def _registry(fn: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("probe", "sonda", Args, fn))
    return registry


def test_unknown_tool_lists_the_available_ones(ctx: ToolContext) -> None:
    result = _registry(lambda c, a: {}).call("nope", {}, ctx)
    assert result["ok"] is False
    assert result["error"]["code"] == "unknown_tool" and "probe" in result["error"]["hint"]


def test_extra_argument_is_an_actionable_error(ctx: ToolContext) -> None:
    result = _registry(lambda c, a: {}).call("probe", {"value": 1, "id": 7}, ctx)
    error = result["error"]
    assert error["code"] == "invalid_arguments"
    assert "id" in error["message"] and "value (obrigatório)" in error["hint"]


def test_missing_and_malformed_arguments_are_errors_not_exceptions(ctx: ToolContext) -> None:
    registry = _registry(lambda c, a: {})
    assert registry.call("probe", {}, ctx)["error"]["code"] == "invalid_arguments"
    assert registry.call("probe", {"value": "x"}, ctx)["error"]["code"] == "invalid_arguments"
    assert registry.call("probe", None, ctx)["error"]["code"] == "invalid_arguments"


def test_tool_error_becomes_data(ctx: ToolContext) -> None:
    def fail(c: ToolContext, a: Args) -> dict[str, Any]:
        raise ToolError("nope", "não deu", "tente isto")

    result = _registry(fail).call("probe", {"value": 1}, ctx)
    assert result == {
        "ok": False,
        "error": {"code": "nope", "message": "não deu", "hint": "tente isto"},
    }


def test_unexpected_exception_never_reaches_the_model(ctx: ToolContext) -> None:
    def boom(c: ToolContext, a: Args) -> dict[str, Any]:
        raise RuntimeError("segredo interno")

    result = _registry(boom).call("probe", {"value": 1}, ctx)
    assert result["error"]["code"] == "internal_error"
    assert "segredo" not in str(result)


def test_success_wraps_the_data(ctx: ToolContext) -> None:
    result = _registry(lambda c, a: {"double": a.value * 2}).call("probe", {"value": 4}, ctx)
    assert result == {"ok": True, "data": {"double": 8}}
