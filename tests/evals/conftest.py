from __future__ import annotations

from pathlib import Path

import pytest
from evals.cases import Case, load_cases
from evals.facts import Facts

from emporio.data.repository import CsvRepository

GOLDEN = Path(__file__).resolve().parents[2] / "evals" / "golden.yaml"


@pytest.fixture(scope="session")
def facts(repo: CsvRepository) -> Facts:
    return Facts(repo)


@pytest.fixture(scope="session")
def cases(facts: Facts) -> list[Case]:
    return load_cases(GOLDEN, facts)


@pytest.fixture(scope="session")
def by_id(cases: list[Case]) -> dict[str, Case]:
    return {case.id: case for case in cases}
