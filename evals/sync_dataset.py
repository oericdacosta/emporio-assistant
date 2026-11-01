"""Publica o golden no LangSmith como o dataset ``emporio-golden`` (upsert idempotente).

Uso:  uv run python -m evals.sync_dataset
O ``evals.run`` já chama isto antes de cada experimento. Cada caso vira um exemplo com id fixo
(derivado do id do caso), então rodar de novo atualiza em vez de duplicar.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Sequence
from typing import Any, Final

from emporio import envfile, observability
from emporio.app import load_data
from emporio.paths import default_data_dir
from evals.cases import Case, load_cases
from evals.facts import Facts

DATASET: Final = "emporio-golden"
_NAMESPACE: Final = uuid.uuid5(uuid.NAMESPACE_URL, "emporio/emporio-golden")


def example_id(case_id: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, case_id)


def build_examples(cases: Sequence[Case]) -> list[dict[str, Any]]:
    """Pura: o que vai ao LangSmith. As expectativas (HARD/SOFT) ficam no código, não no dataset."""
    return [
        {
            "id": example_id(case.id),
            "inputs": {"turns": list(case.turns), "now": case.now},
            "metadata": {"id": case.id, "suite": case.suite, "intent": case.intent},
        }
        for case in cases
    ]


def sync(client: Any, cases: Sequence[Case]) -> int:
    """Cria o dataset se faltar, cria os exemplos novos, atualiza os que já existem e remove os
    de casos que saíram. ``create_examples`` com id fixo devolve 409 se o exemplo já existe, então
    o upsert é feito à mão: criar só o que falta, atualizar o resto."""
    if not client.has_dataset(dataset_name=DATASET):
        client.create_dataset(DATASET, description="Casos de referência do Téo (evals/golden.yaml)")
    examples = build_examples(cases)
    existing = {str(e.id) for e in client.list_examples(dataset_name=DATASET)}
    new = [e for e in examples if str(e["id"]) not in existing]
    if new:
        client.create_examples(dataset_name=DATASET, examples=new)
    for e in examples:
        if str(e["id"]) in existing:
            client.update_example(e["id"], inputs=e["inputs"], metadata=e["metadata"])
    keep = {str(e["id"]) for e in examples}
    stale = [e for e in existing if e not in keep]
    if stale:
        client.delete_examples(stale)
    return len(examples)


def main() -> int:
    from pathlib import Path

    envfile.load(Path(".env"))
    if observability.setup() != "ativo":
        print("Erro: sem LANGSMITH_API_KEY não há onde publicar o dataset.", file=sys.stderr)
        return 3
    from langsmith import Client

    repo, _ = load_data(default_data_dir())
    count = sync(Client(), load_cases(Path(__file__).with_name("golden.yaml"), Facts(repo)))
    print(f"{count} exemplos em '{DATASET}'", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
