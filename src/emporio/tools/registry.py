"""Registro das tools: validação dos argumentos, despacho e erros como dados.

O modelo só enxerga o mundo por aqui. Argumento inválido ou falha esperada volta como
``{"ok": False, "error": {...}}`` para ele se corrigir; nenhuma exceção chega ao modelo.
Dinheiro entra como ``Decimal`` e sai sempre como texto formatado (``"R$ 2.199,00"``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, WithJsonSchema

from emporio.tools.deps import ToolContext

log = logging.getLogger(__name__)

ToolResult = dict[str, Any]

Money = Annotated[Decimal, Field(ge=0), WithJsonSchema({"type": "number", "minimum": 0})]
PositiveMoney = Annotated[
    Decimal, Field(gt=0), WithJsonSchema({"type": "number", "exclusiveMinimum": 0})
]

_NULL: Final = {"type": "null"}


class Params(BaseModel):
    """Base dos argumentos: campo que o modelo inventar é erro, não é ignorado."""

    model_config = ConfigDict(extra="forbid")


class ToolError(Exception):
    """Falha esperada de uma tool; vira ``ok: false`` com código, mensagem e dica."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


@dataclass(frozen=True, slots=True)
class Tool[P: BaseModel]:
    name: str
    description: str  # diz quando usar e quando NÃO usar
    params: type[P]
    fn: Callable[[ToolContext, P], dict[str, Any]]
    enums: Mapping[str, Sequence[str]] = field(default_factory=dict)  # valores gerados dos dados


def failure(code: str, message: str, hint: str = "") -> ToolResult:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def _clean(node: Any, defs: Mapping[str, Any]) -> Any:
    """Deixa o JSON Schema enxuto: sem títulos, sem ``$ref`` e opcional sem ``anyOf`` com null."""
    if isinstance(node, list):
        return [_clean(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _clean(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    node = {k: v for k, v in node.items() if k not in ("title", "$defs")}
    options = node.get("anyOf")
    if options and _NULL in options and len(options) == 2:
        node.pop("anyOf")
        node.pop("default", None)
        node.update(next(o for o in options if o != _NULL))
    return {k: _clean(v, defs) for k, v in node.items()}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool já registrada: {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [self._spec(tool) for tool in self._tools.values()]

    def call(self, name: str, raw_args: Any, ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return failure(
                "unknown_tool",
                f"não existe a tool '{name}'",
                f"Tools disponíveis: {', '.join(self._tools)}.",
            )
        try:
            args = tool.params.model_validate(raw_args)
        except ValidationError as exc:
            return failure("invalid_arguments", _describe(exc), _accepted(self._spec(tool)))
        try:
            return {"ok": True, "data": tool.fn(ctx, args)}
        except ToolError as exc:
            return failure(exc.code, exc.message, exc.hint)
        except Exception:
            log.exception("falha inesperada na tool %s", name)
            return failure(
                "internal_error",
                "a consulta falhou por um problema nosso",
                "Peça desculpas, não invente a informação e ofereça encaminhar para a equipe.",
            )

    def _spec(self, tool: Tool[Any]) -> dict[str, Any]:
        schema = tool.params.model_json_schema()
        schema = _clean(schema, schema.get("$defs", {}))
        for name, values in tool.enums.items():
            prop = schema["properties"][name]
            prop["type"] = "string"
            prop["enum"] = list(values)
        schema.setdefault("properties", {})
        return {"name": tool.name, "description": tool.description, "parameters": schema}


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in e['loc']) or 'argumentos'}: {e['msg']}"
        for e in exc.errors(include_url=False)
    )


def _accepted(spec: dict[str, Any]) -> str:
    schema = spec["parameters"]
    required = set(schema.get("required", ()))
    fields = [f"{n} (obrigatório)" if n in required else n for n in schema["properties"]]
    return f"Argumentos aceitos: {', '.join(fields) or 'nenhum'}."
