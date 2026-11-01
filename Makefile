.PHONY: install chat ui lint fmt type test cov eval eval-regression eval-capability eval-regress sync-dataset policies clean

install:
	uv sync --locked

chat:  # abre o chat; a chave, o modelo e o relógio vêm do .env
	uv run emporio

ui:  # interface web; a chave, o modelo e o relógio vêm do .env
	uv run streamlit run src/emporio/web/app.py

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

eval-regression:  # o portão: só os casos que o agente já acerta, todos têm de passar (sai com 1)
	uv run python -m evals.run --suite regression --repeat 3

eval-capability:  # os casos difíceis: mede o quanto falta, não quebra
	uv run python -m evals.run --suite capability --repeat 3

eval-regress:  # compara com a referência antes de mexer em prompt ou modelo
	uv run python -m evals.run --repeat 3 --baseline evals/reports/baseline.json

sync-dataset:  # publica o golden no LangSmith como 'emporio-golden' (precisa de LANGSMITH_API_KEY)
	uv run python -m evals.sync_dataset

policies:
	uv run scripts/extract_policies.py data/raw/politicas_da_loja.pdf data/policies.md

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis .coverage htmlcov dist build
