.PHONY: install chat lint fmt type test cov eval eval-regress policies clean

install:
	uv sync --locked

chat:  # abre o chat; a chave, o modelo e o relógio vêm do .env
	uv run emporio

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

EVAL_REPEAT ?= 1

eval:  # chama o modelo de verdade: custa tokens e não roda no CI
	uv run python -m evals.run --repeat $(EVAL_REPEAT)

eval-regress:  # compara com a referência antes de mexer em prompt ou modelo
	uv run python -m evals.run --repeat 3 --baseline evals/reports/baseline.json

policies:
	uv run scripts/extract_policies.py data/raw/politicas_da_loja.pdf data/policies.md

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis .coverage htmlcov dist build
