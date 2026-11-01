.PHONY: install lint fmt type test cov policies clean

install:
	uv sync --locked

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

type:
	uv run mypy

test:
	uv run pytest -q

cov:
	uv run pytest -q --cov=emporio --cov-branch --cov-report=term-missing

policies:
	uv run scripts/extract_policies.py data/raw/politicas_da_loja.pdf data/policies.md

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis .coverage htmlcov dist build
