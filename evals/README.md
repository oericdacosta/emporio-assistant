# Agent evaluation

Measures the agent with the real model and produces a versionable report. It does not run in the CI
(it costs API calls and the model varies); the CI only checks that the golden set is consistent with
the data.

```bash
make eval                      # 1 run per case
make eval EVAL_REPEAT=3        # 3 per case: a case only passes if it passes all three
make eval-regression           # the gate: only the regression suite, 3 per case; exits 1 if any fails
make eval-capability           # only the capability suite: measures what is missing, never breaks
make eval-regress              # compares with evals/reports/baseline.json before touching the prompt or the model
uv run python -m evals.run --only G1 --label after-the-fix   # does not overwrite the report of the same day
```

The key comes from `OPENAI_API_KEY` and the model from `LLM_MODEL` (or from the `.env` file). Prices
per million tokens are optional and given by whoever runs it; without them the report only shows tokens.

## LangSmith

With `LANGSMITH_API_KEY` in `.env` (see `.env.example`), `evals.run` does three more things:

1. **Dataset**: publishes the golden set as `emporio-golden` (`evals/sync_dataset.py`, also through
   `make sync-dataset`). Each case is an example with a fixed id, `inputs {turns, now}` and
   `metadata {id, suite, intent}`; running again updates instead of duplicating. The expectations
   (HARD/SOFT) stay in the code, not in the dataset.
2. **Experiment**: runs `langsmith.evaluate()` with one case per example (`--repeat` becomes
   `num_repetitions`, `max_concurrency=1`). The evaluators are code, with no LLM as a judge:
   `hard` (0/1, with the reasons in the comment) and `soft` (fraction of the expected phrases). A
   summary evaluator computes pass@k and pass^k per suite and per intent. Every trace carries the tags
   `suite:<...>` and `case:<id>`; the experiment metadata carries suite, model, commit and prompt
   hash. The tools appear in full, unmasked.
3. **Report**: the Markdown in `reports/` is still generated and committed (it is the evidence in the
   repository) and now carries the name and the link of the experiment.

**The gate does not depend on LangSmith.** The report, `latest.json`, `--baseline` and the exit code 1
of the regression suite come from the local runs (the same ones the experiment evaluates). Without a
key, or if LangSmith fails, the evals run locally as before, with the warning "No experiment was
created"; whatever LangSmith did not complete runs locally.

## The two suites

Each case of `golden.yaml` has `suite:` (the default is `regression`).

- **regression**: what the agent already gets right. It has to stay at 100%: a case that fails makes
  `evals.run` exit with 1 (`make eval-regression`). Since calling the model costs API usage, the gate
  runs by hand or on demand, not in the CI.
- **capability**: the hard cases, not yet mastered. They only measure how much is missing and do not
  break the run. A case moves up to `regression` when it passes every run for a while.

The report shows, per case and per intent, pass@1 (rate per run), pass@k (at least one of the k
passed) and pass^k (all passed, which is what matters in customer service). With k=3 the numbers are
coarse: a case with 1/3 and another with 2/3 weigh almost the same, and there is no confidence interval.

### Current state (2026-11-01, `gpt-4.1-mini`, 3 runs per case)

- **regression: 29/29.**
- **capability: 0/1.** M01 (PIX and 12x in sequence) is unstable: in the third turn the model
  answered correctly, fell into the safe answer and, in one run, quoted an installment plan that does
  not match the product's (10x of R$ 104.90 instead of 12x of R$ 183.25). The cause was not
  investigated: the report does not yet record what the guard blocked, and the guard does not cover
  the value of another product.
- Before the error analysis the report showed 25/30. Three failures were the agent's (G12, G16, G17,
  fixed in the tool descriptions and in the prompt) and one was the case's (G27, which punished the
  general rule being quoted correctly). The history is in `reports/`.

## The golden set

`golden.yaml` has 30 cases: 27 with a single question and 3 multi-turn conversations. No value is
typed by hand. `{price:95}`, `{pix:95}`, `{installment:95:12}` and the other markers are computed
from the CSVs (`facts.py`), and `assume` declares the state of the data that the case depends on
(product out of stock, active promotion); a test checks it. If the data changes, the golden set
follows or warns.

- **HARD** fails the case: tool called or not, exact value, forbidden term, safe answer delivered in
  place of the answer, robotic tone.
- **SOFT** is only reported: expected phrases, which change from model to model, and completeness
  items (for example "interest free").

Patterns are regexes, compared with the answer in lowercase and without accents. An item in a list
means "any of these".

## Reading the report

`reports/YYYY-MM-DD_<model>.md` shows the result per case, the failures with the agent's answer, the
warnings, the tokens, the latency and how many times the grounding guard acted. A case marked
"unstable" passed in some runs and failed in others: the model is not deterministic even at
temperature zero. `latest.json` keeps what passed, for the comparison with the baseline.

The report does not hide failures: the ones that show up feed the correction of the agent. An
expectation of the golden set is only changed when the agent was right and the expectation was too narrow.
