"""Saída da CLI: a conversa vai ao stdout; banner, progresso, trace e erros vão ao stderr."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, TextIO

from emporio.agent.loop import Event, ToolFinished, ToolStarted, TurnResult
from emporio.agent.turn_log import mask
from emporio.cli.text import AGENT_NAME, TOOL_LABELS

SUMMARY_CHARS = 280


class Style:
    """Cores ANSI só em terminal, respeitando NO_COLOR e TERM=dumb."""

    def __init__(self, stream: TextIO) -> None:
        self.on = (
            stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        )

    def __call__(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.on else text


def summarize(result: dict[str, Any], limit: int = SUMMARY_CHARS) -> str:
    body = result.get("data") if result.get("ok") else result.get("error")
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Ui:
    def __init__(self, *, trace: bool, debug: bool) -> None:
        self.trace = trace
        self.debug = debug
        self._out, self._err = sys.stdout, sys.stderr
        self._o, self._e = Style(self._out), Style(self._err)
        self._arguments: dict[str, Any] = {}
        self._transient = False

    # --- saída ---------------------------------------------------------------------------------

    def say(self, text: str = "") -> None:
        print(text, file=self._out, flush=True)

    def note(self, text: str, code: str = "2") -> None:
        print(self._e(code, text), file=self._err, flush=True)

    def error(self, text: str) -> None:
        print(self._e("1;31", f"Erro: {text}"), file=self._err, flush=True)

    def prompt(self) -> str:
        if not self._o.on:
            return "Você> "
        return (
            "\001\x1b[1;32m\002Você>\001\x1b[0m\002 "  # \001..\002: o readline não conta os escapes
        )

    def reply(self, text: str) -> None:
        self.say(f"{self._o('1;36', AGENT_NAME + '>')} {text}\n")

    def echo(self, text: str) -> None:
        """Mostra a fala do cliente quando ela não foi digitada num terminal."""
        self.say(f"Você> {text}")

    # --- progresso -----------------------------------------------------------------------------

    def busy(self, text: str) -> None:
        """Linha que some sozinha, só em terminal e sem o trace ligado."""
        if self._err.isatty() and not self.trace:
            self._err.write(f"\r\x1b[2K{self._e('2', '  ' + text)}")
            self._err.flush()
            self._transient = True

    def idle(self) -> None:
        if self._transient:
            self._err.write("\r\x1b[2K")
            self._err.flush()
            self._transient = False

    def on_event(self, event: Event) -> None:
        """Recebe os eventos do loop do agente."""
        if isinstance(event, ToolStarted):
            self._arguments = event.arguments
            label = TOOL_LABELS.get(event.name, "consultando o sistema")
            self.busy(f"{AGENT_NAME} está {label}...")
        elif isinstance(event, ToolFinished) and self.trace:
            status = "ok" if event.ok else "erro"
            arguments = json.dumps(mask(self._arguments), ensure_ascii=False)
            self.note(
                f"  [ferramenta] {event.name} {arguments} -> {status} "
                f"{event.seconds * 1000:.0f}ms: {summarize(event.result)}"
            )
            self._arguments = {}

    def turn_summary(self, result: TurnResult) -> None:
        usage = result.usage
        notes = ""
        if result.guard_blocked:
            notes += " | a guarda de fundamentação barrou uma resposta"
        if result.fallback:
            notes += " | resposta segura no lugar"
        self.note(
            f"  [turno] {len(result.tools)} consulta(s) | {result.llm_calls} chamada(s) ao modelo | "
            f"tokens entrada/saída/cache: {usage.input_tokens}/{usage.output_tokens}/{usage.cached_tokens}"
            f" | {result.seconds * 1000:.0f} ms | trace_id={result.trace_id}{notes}"
        )
