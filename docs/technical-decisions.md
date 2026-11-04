# Technical decisions

The challenge asks for the choices to be justified: "there is no right answer, we want to understand your
reasoning". This document records each relevant decision in the same format:

- Decision: what was chosen.
- Why: the reason, with project numbers when they exist.
- Discarded alternatives: what was considered, and why not.
- Accepted cost: what is lost with the choice.
- When I would revisit it: what would make the decision change.

To see *how* everything works inside, read [architecture.md](architecture.md).

## Contents

| # | Decision | In one line |
|---|---|---|
| [D1](#d1-the-approach-function-calling-with-deterministic-tools) | Agent approach | Function calling + deterministic tools, no RAG |
| [D2](#d2-what-the-model-does-and-what-the-code-does) | Division of responsibilities | The model talks, the code decides |
| [D3](#d3-turn-orchestration-from-a-hand-written-loop-to-langgraph) | Orchestration | From a hand-written loop to a LangGraph graph |
| [D4](#d4-model-and-provider) | Model and provider | OpenAI `gpt-4.1-mini` behind our own contract |
| [D5](#d5-data-handling) | Data | CSVs as immutable objects, `Decimal`, no pandas and no database |
| [D6](#d6-policy-lookup-without-embeddings) | Policies | One tool per topic, no embeddings |
| [D7](#d7-the-grounding-guard) | Anti-hallucination | Ledger of values, deterministic, no LLM as a judge |
| [D8](#d8-the-scope-router-before-the-model) | Out of scope and injection | Rules before the model |
| [D9](#d9-customer-identity) | Identity | Order plus a proof, verified in code |
| [D10](#d10-history-persistence) | History | SQLite, no personal data, 30 days |
| [D11](#d11-conversation-memory-the-working-state) | Memory | Working state in code, no LLM summary |
| [D12](#d12-interface) | Interface | CLI and Streamlit, a single command |
| [D13](#d13-no-text-streaming) | Streaming | Only progress events |
| [D14](#d14-evaluation) | Evaluation | Deterministic golden set, 3 runs, no LLM as a judge |
| [D15](#d15-observability) | Observability | LangSmith that never takes the service down |
| [D16](#d16-injectable-clock) | Time | Injectable clock and fixed time zone |
| [D17](#d17-engineering-and-process) | Engineering | `uv`, strict types, CI with 5 gates, real PRs |
| [D18](#d18-prompt-strategy) | Prompt | Short, in sections, with a stable prefix and synthetic examples |
| [Annex](#annex-technologies-we-do-not-use-and-when-they-would-start-to-make-sense) | What we do not use | Table of technologies and when they would make sense |

---

## D1. The approach: function calling with deterministic tools

**Decision.** Téo is a *function calling* agent: the model decides which tools to call, the tools are
deterministic Python functions over the store's data, and the model writes the answer with what they
returned.

**Why.** Looking at the problem, not at the trend:

| Characteristic of the problem | What it demands |
|---|---|
| Small, structured data (65 products, 50 customers, 20 orders, 25 promotions) | Exact lookup, not similarity |
| "Up to R$ 1000" is a numeric filter | An operation on a number, not on text |
| Rules with arithmetic: PIX does not stack with a promotion, minimum per installment, 7 calendar days, opening hours and holidays | Testable code, not the model counting |
| Personal data (orders and customers) | Control over who sees what |
| Traps in the data (discontinued with stock, "coming soon" with stock, active with zero stock) | An explicit business rule |
| An 8-page manual | Fits in a few tokens and is consulted on demand |

```mermaid
flowchart TD
    Q(["Customer question"]) --> N{"What is the nature of the information?"}
    N -->|"Price, stock, promotion"| A["Catalog tool: exact lookup in the CSVs"]
    N -->|"Arithmetic: installment, PIX, deadline, shipping, opening hours"| B["Domain function: Decimal and dates, in code"]
    N -->|"Rule text: exchange, warranty, payment"| C["search_policy: literal section of the manual"]
    N -->|"Order or customer data"| D["Identity in code, then get_order_status"]
    N -->|"None of these: out of scope"| E["Polite refusal, without consulting anything"]
    A & B & C & D --> F["The model writes with the result already given"]
    E --> F
```

**Discarded alternatives.**

| Alternative | What it would solve | Why it does not fit here |
|---|---|---|
| RAG with embeddings over CSV and PDF | Search in long text | Price and stock would become text retrieved by similarity: "up to R$ 1000" is not semantics; product rows may come back stale; the PIX rule requires arithmetic; there is no control over personal data. A vector store for 8 pages is infrastructure with no gain. |
| The whole context in the prompt (CSV and manual fit in ~15 to 20 thousand tokens) | Zero infrastructure | The data gets "frozen" in the prompt, against the rule of never informing without consulting; personal data of 50 customers in every call; cost per turn; and the model would do the arithmetic. It works as a comparison *baseline*, not as a product. |
| Text-to-SQL | Free-form questions over the data | The model would write SQL over `customers` and `orders`: personal data leaks and injection; the business rules (effective price, derived availability) are not in the schema, and the model would reimplement them wrongly; hard to test. |
| ReAct in text (Thought, Action, Observation) | Explicit reasoning | Fragile parsing, and the "Thought" spends tokens and may leak to the customer. Native *function calling* is the same cycle with validated JSON. |
| Multi-agent (router plus specialists) | Large domains | There are only three small domains. It would cost one extra call per turn, one more point of failure and context loss between agents. Mixed questions ("I want the GD20, can I pay in 10x and ship to Dourados?") cross the domains in the same turn. The separation stayed logical, through tool modules. |
| Fine-tuning | Tone and format | The data changes (price, stock) and what the model lacks is not style, it is fact. Tone is solved with a prompt. |

**Accepted cost.** The system depends on a model with good *tool calling*. The mitigation is to measure
(D14) and to have the defense layers (D7) for when it fails.

**When I would revisit it.** If the catalog grew past thousands of items with long descriptions, a
similarity search would come in only for the catalog, keeping the rules in code. If the manual grew to
dozens of documents, then yes, embeddings for the policies.

---

## D2. What the model does and what the code does

This is the decision that organizes all the others. The question that guided each module was: *"does this
have a right answer?"* If it does, it is code.

```mermaid
flowchart LR
    subgraph MODELO["Model: where there is ambiguity"]
        M1["Understand slang and typos"]
        M2["Choose the tool"]
        M3["Build the arguments"]
        M4["Write in the persona"]
    end
    subgraph CODIGO["Code: where there is a rule"]
        C1["Money: Decimal and HALF_UP"]
        C2["Dates: injectable clock"]
        C3["Authorization: identity"]
        C4["Validate what the model said"]
    end
    MODELO -->|"calls"| CODIGO
    CODIGO -->|"result already given"| MODELO
```

**Why.** A language model produces plausible text, not correct text. For the store, a wrong price or a
return approved past the deadline is the kind of error that costs money and trust. In code, each rule has
a test (864 tests in total) and gives the same result in every run.

A concrete example. The prompt tells the model: *"Never do arithmetic in your head: values, installments,
dates and deadlines come from the tools. State the return verdict that the tool returns; do not count
days yourself."* (The prompt itself is written in Portuguese.) And the guard (D7) checks that it obeyed.

---

## D3. Turn orchestration: from a hand-written loop to LangGraph

**Decision.** Each turn runs as a LangGraph graph (`agent/graph.py`) with seven nodes: `router`,
`resume_flows`, `model`, `tools`, `guard`, `compose` and `fallback`.

This is a decision that changed during the project, and the path is worth telling.

```mermaid
flowchart LR
    subgraph ANTES["Oct 31: hand-written loop"]
        L1["model"] --> L2["tools"] --> L1
        L1 --> L3["guard"] --> L4["retry or fallback"]
    end
    subgraph DEPOIS["Nov 1: LangGraph graph"]
        G1["router"] --> G2["resume_flows"] --> G3["model"]
        G3 <--> G4["tools"]
        G3 --> G5["guard"]
        G5 --> G6["compose"]
        G5 --> G7["fallback"]
    end
    ANTES -->|"the flow gained branches"| DEPOIS
```

The initial plan was a hand-written loop of about 60 lines over the SDK, with no framework: the argument
was that a framework would hide exactly the decisions the challenge wants to evaluate (iteration limit,
error handling, guard). That was the first version, and it worked.

What changed. The flow stopped being a simple loop: it gained a router that answers without the model,
resumption of flows by the orchestrator, a guard retry, a last iteration that is mandatorily without
tools, and a fallback. With so many branches, the flow control was scattered in `if`s inside a loop, hard
to read. In a graph, each branch is a named edge and the diagram of the code is the diagram of the flow
(the docstring of `graph.py` draws it). It was also the basis for per-node tracing in LangSmith.

How LangGraph is used. Only as an orchestrator: compiled without a checkpointer on purpose, because the
history belongs to our own SQLite, which never stores a tool result or the customer verification. The
mutable state of the turn (`Ledger`, `Meter`) is passed by reference. The model is the `LLMClient`, the
tools come from the `Toolbox`, and the steps of running tools and closing the turn are in `steps.py`,
testable without LangGraph.

| Alternative | Why not |
|---|---|
| Keep the hand-written loop | It worked, but the flow control became unreadable with six branches |
| Full LangChain | Disproportionate abstraction and API churn; only the graph is used |
| PydanticAI | A good alternative, typed and with `output_validator`; it would hide the loop and the guard, which are the focus |
| OpenAI Agents SDK | Designed for one provider, and its *handoffs* are the multi-agent design we discarded |
| Own state machine | It would reinvent what LangGraph already does well |

**Accepted cost.** One more dependency (and its transitive ones) and the learning curve of LangGraph. It is
the cost of a revised decision: the architecture rule (the agent does not know the concrete tools) made
the swap cost one commit, without touching tools, domain or tool tests.

**When I would revisit it.** If the flow became linear again, I would remove LangGraph. If I needed graph
state persistence or a human in the loop (approval before an action), the checkpointer would start to pay
off.

---

## D4. Model and provider

**Decision.** OpenAI as the provider, model `gpt-4.1-mini`, chosen by configuration (`LLM_MODEL`), behind
our own one-function contract (`LLMClient`).

```mermaid
flowchart TD
    subgraph CONTRATO["Contract: LLMClient.chat"]
        P["One-function Protocol"]
    end
    AG["Agent"] --> P
    P --> O["OpenAIClient: implemented and measured"]
    P --> R["ReplayLLM: demo and examples"]
    P --> F["FakeLLM: hermetic tests"]
    P -.->|"would swap without touching the agent"| X["Another provider: Anthropic, Gemini, local"]
```

**Why.** The criterion was measured, not assumed. Before writing the agent, a *spike*
(`scripts/spike_tool_calling.py`) ran the four questions of the challenge against the real tools and
measured calls, arguments, latency and tokens. Afterwards, the golden set (D14) compared the versions:

| Measure (golden, 37 cases × 3 runs) | Result |
|---|---|
| Cases passed | 35 of 37 |
| Median latency of a case | 1.7 s (maximum 6.4 s) |
| Cost per turn (real use and tests) | about US$ 0.0017 |
| Input coming from the provider cache | 89% in the last golden round |

A small, cheap model was enough because the hard work is not in the model: price, deadline and identity
are code. This follows directly from D2.

The contract (`LLMClient`) isolates the provider: changing model is changing `LLM_MODEL`; changing provider
is writing an adapter of about 100 lines. The same contract allows `ReplayLLM` (which replays recorded
conversations) and the tests' `FakeLLM`, and that is why the whole suite runs without network and without a
key.

No default model. `LLM_MODEL` is required, on purpose: model names age, and a clear configuration request is
better than a "model does not exist" error on the first message.

**Alternatives.**

| Alternative | Assessment |
|---|---|
| Free, local model (Ollama) | Zero cost and total privacy, but the quality of *tool calling* and of Portuguese in small models was not measured here. It would be the path if the data could not leave the store's infrastructure. |
| Free tier of another provider | Usage limits per minute and per day tend to be tight for an agent that makes 2 or more calls per turn with a prompt of ~10 thousand tokens. For an evaluated prototype, predictability is worth more than zero cost. |
| A larger model | More expensive and slower; only if the small one failed the evals, which did not happen enough to justify it. |
| LiteLLM or a ready-made multi-provider layer | A heavy dependency for a single provider; the official SDK and our own contract are enough. |
| Automatic fallback between providers | Hides the cause of the failure and duplicates the behavior to test. |

**Accepted cost.** Only the OpenAI adapter was implemented and no other provider was compared: the choice
rests on the measures above, not on a side-by-side comparison. This is recorded in
[limitations-and-evolution.md](limitations-and-evolution.md).

**When I would revisit it.** If cost or latency got tight at scale, I would measure an even smaller model
(or a local model) on the same golden set, which exists precisely for that comparison. If the data policy
required it, I would change the provider without touching the agent.

---

## D5. Data handling

**Decision.** The original CSVs stay intact in `data/raw/`. On load, each row becomes an immutable,
validated object (Pydantic, `frozen=True`). Monetary values use `Decimal` with `HALF_UP` rounding. The load
produces a quality report that points out inconsistencies without silently fixing them.

```mermaid
flowchart LR
    CSV[("Original CSVs, intact")] --> LOAD["Load with Pydantic validation"]
    LOAD -->|"invalid row"| REJ["Rejected, with file, line and reason"]
    LOAD --> OBJ["Immutable objects: Product, Order, Customer, Promotion"]
    OBJ --> QR["Quality report: points out, does not fix"]
    OBJ --> DOM["domain: pure business rules"]
    MAN[("Policy manual as a PDF")] --> EXT["scripts/extract_policies.py"]
    EXT --> MD["data/policies.md: one section per chapter"]
    MD --> RULES["policy_rules.json: numbers of the manual plus the sha256 of the text"]
    MD --> NOTES["policies_notes.json: decisions about the contradictions"]
    RULES --> DOM
    RULES -->|"did the manual change?"| DRIFT["emporio --check-data and a test fail until someone reviews"]
```

**Why.**

- Do not fix the CSVs. Fixing erases the source. The quality report records what looks odd (3 products
  with inconsistent status and stock, 2 orders whose total differs from the sum of the items, 3 orders
  with an installment below the floor, 1 e-mail repeated between customers, 3 categories without products),
  and the agent deals with it: it treats `total_brl` as the "recorded total", and the product status takes
  precedence over stock.
- `Decimal`, never `float`. `0.1 + 0.2` in `float` is not `0.3`; with money, that becomes a wrong cent.
  The `money()` function rejects `float` on purpose.
- The numbers of the manual are not typed in the code. Deadline, PIX discount, installment tiers, shipping
  and opening hours come from `policy_rules.json`, which keeps the `sha256` of the `policies.md` they came
  from. If the manual changes, the check fails until someone verifies the values and updates the hash. It
  is a protection against silent drift between the text and the code.
- The product description does not enter the search. Some products have a description that contradicts
  their own name, so the search uses name, category, aliases and specifications, and leaves the
  description out.

| Alternative | Why not |
|---|---|
| pandas | A large dependency for tables of tens of rows; the standard library is enough |
| SQL database (SQLite or Postgres) populated from the CSVs | No gain at this volume, and it adds a state to keep in sync |
| Let the model interpret `status` and `stock_quantity` | It is the source of the "active with zero stock" error |
| Edit the CSVs to clean them | Loses the source and hides the problem |
| Constants typed in the code | They drift from the manual without anyone noticing |

The details of each inconsistency and how it is handled are in [data-and-policies.md](data-and-policies.md).

**Accepted cost.** The data is loaded once at startup: updating a price requires a restart. For a
prototype with static files it works; in production it becomes a query to a live system
(see [limitations-and-evolution.md](limitations-and-evolution.md)).

---

## D6. Policy lookup, without embeddings

**Decision.** The manual becomes Markdown sections, one per chapter, and the `search_policy` tool takes up
to 3 topics from an enum (`empresa`, `horario`, `pagamento`, `trocas_devolucoes`, `frete_entrega`,
`promocoes`...) and returns the literal text of the sections, plus the decision notes of the topic and how
much of the question the manual covers.

```mermaid
flowchart TD
    Q["Customer: I regret it, can I return?"] --> M["Model chooses the topics: trocas_devolucoes"]
    M --> SP["search_policy"]
    SP --> SEC["Literal sections of the manual"]
    SP --> NOTE["Decision notes: contradictions already resolved"]
    SP --> COV["Coverage: how much of the question the manual answers"]
    SEC & NOTE & COV --> M2["Model answers only with what the text says"]
    COV -->|"partial or no coverage"| ESC["Do not fill the gap: hand over to the team with register_complaint"]
```

**Why.**

- Deterministic routing. The model chooses from an enum, so the topic is the same for the same intent and
  shows up in the trace: it visibly demonstrates that the agent "knows when to consult policies".
- Literal text. The model does not paraphrase a deadline; it quotes what is written.
- The contradictions of the manual are decided once, in `policies_notes.json`, and each returned section
  carries the notes of its topic. The model never has to choose between two versions.
- Coverage. When the question is not in the manual (instrument repair, parking, whether animals are
  accepted), the tool says so, and the rule is not to fill the gap: hand over to the team.

| Alternative | Why not |
|---|---|
| RAG with embeddings | Infrastructure with no gain for 10 sections, and it does not solve "up to R$ 1000" |
| Keyword search in the text | Depends on the customer using the manual's word: whoever says "I regret it" does not write "return" |
| The whole manual in the prompt | Cost in every call and no trace of *when* the agent consulted |
| A summary of the policies in the prompt | The summary diverges from the manual; only a short summary of the inviolable rules remains |

**Accepted cost.** The list of topics is maintained by hand: a new topic requires touching the enum. In an
8-page manual, this gives control over what the model can ask for.

**When I would revisit it.** With dozens of policy documents, by region or by brand, semantic search would
start to pay off. Even then I would put it behind the same tool, without changing the contract.

---

## D7. The grounding guard

**Decision.** Before sending, the code checks that every value in reais, percentage, installment and
tracking code in the answer has a source: it came from a tool, was said by the customer, or was said before
and approved (the `Ledger`). If it does not, the model receives a correction and tries once; if it fails
again, the customer receives a safe answer built with what the tools returned.

```mermaid
flowchart TD
    R["Model draft"] --> V{"Any value without a source in the ledger?"}
    V -->|"no"| OK["Continues to the other filters"]
    V -->|"yes, 1st time"| C["Correction to the model: use the tool's value or say you will confirm"]
    C --> R2["New draft"]
    R2 --> V2{"Still a value without a source?"}
    V2 -->|"no"| OK
    V2 -->|"yes"| FB["Safe answer, built from the tools"]
```

**Why.** The store's rule is: *never state a price, stock or deadline without consulting the system*. Only
saying that in the prompt guarantees nothing. The guard turns the rule into a check that is deterministic,
cheap (regular expressions, no extra call) and identical in every run.

**Discarded alternatives.**

| Alternative | Why not |
|---|---|
| Only instruct in the prompt | It is what the model "should" do, not what it does |
| A second LLM as a judge | Cost and latency on every answer, a non-deterministic result, and the judge also errs |
| Block the whole turn | A worse experience for the customer, with no gain |
| Check only the `R$` prefix | It would let percentages, installments and values written as "1234 reais" through |

What it covers and what it does not. It covers `R$ 1.234,56`, `1234 reais`, `2 mil reais`, percentages,
`12x`, tracking `BR...BR`, protocol `EMP-...`, values in currencies the store does not use (US$, €) and
state phrases that only a tool can assert ("I could not confirm your data"). It does not cover deadlines in
days, stock, dates, numbers written out in words ("dois mil") or a swapped association (the price of
product A given for B). The stress test showed that the remaining failures are outside this reach, and that
is why the next layers exist (filters, intents and the "no tool, no fact" gate; see
[architecture.md](architecture.md#7-the-defense-layers)).

The "no tool, no fact" gate. When no tool ran in the turn, the answer is long and is not just a question, a
detector (a minimal call to the same model: no tools, temperature 0, 4 tokens) decides whether it states
store facts. For store services (repair, lessons, parking) there is a deterministic rule that makes the
detector unnecessary. Any failure of the detector counts as "does not state": the gate never takes a turn
down.

**Accepted cost.** False positives exist: in the evaluation rounds, the guard blocked 14 runs out of 111
(most fix themselves on the retry; one became a safe answer). The stress tests also showed that each new
defense can create a new defect, which was measured and documented (see [evaluation.md](evaluation.md)).

---

## D8. The scope router before the model

**Decision.** A set of deterministic rules, applied to the message before the model, answers with canned
text to three classes: prompt injection, an out-of-scope request (code, recipe, election, essay,
translation) and a health emergency (points to SAMU, the Brazilian emergency service).

```mermaid
flowchart TD
    T["Customer message"] --> N["Normalize: lowercase, no accents"]
    N --> E{"Emergency?"}
    E -->|"yes"| ER["Guides: SAMU 192, no model"]
    E -->|"no"| I{"Injection?"}
    I -->|"yes"| IR["Short refusal, no model"]
    I -->|"no"| S{"Mentions something about the store?"}
    S -->|"yes"| MOD["Goes on to the model"]
    S -->|"no"| O{"Out of scope?"}
    O -->|"yes"| OR["Refuses and redirects, no model"]
    O -->|"no"| MOD
```

**Why.** A canned answer is cheaper, faster and does not depend on the model obeying the prompt. The
central care: `out_of_scope` never acts on a message that mentions something about the store ("violão",
"pedido", "preço"), and `injection` and `emergency` always act. That way "I want a guitar and a cake
recipe" is not blocked.

**Accepted cost.** They are rules, not a classifier. The patterns are conservative on purpose: it is better
to let something pass than to block a real customer, and the model (with the prompt and the output filters)
remains the safety net for what escapes. The router only looks at the current message, not the history, and
the list of foreign destinations is short.

| Alternative | Why not |
|---|---|
| LLM classifier | An extra call on every message, for a problem that rules solve in most cases |
| Prompt only | In the stress test, the model went as far as writing Python code on request and dumping the tool schema; the prompt alone did not hold |
| Moderation library | It solves another problem (harmful content), not "is this a store subject?" |

---

## D9. Customer identity

**Decision.** For any order data, the customer provides the order number and a proof: the e-mail on file
or the last 4 digits of the phone. The code decides (`identity.py`); the model only passes along what the
customer typed.

```mermaid
flowchart TD
    IN["Order and proof from the customer"] --> LK{"Session locked, or order locked?"}
    LK -->|"yes"| DENY["Denies, without counting and without saying why"]
    LK -->|"no"| CMP["Compares the proof in constant time, even if the order does not exist"]
    CMP --> OK{"Does the proof match the owner of the order?"}
    OK -->|"yes"| VER["Verified: keeps the customer in the session and returns only the first name"]
    OK -->|"no"| FAIL["Counts the failure on the session and on the order"]
    FAIL --> LIM{"3 failures on the session or 5 on the order?"}
    LIM -->|"yes"| LOCK["15-minute lockout"]
    LIM -->|"no"| DENY2["Denies: the same answer as always"]
```

**Why.** Orders are numbered in sequence (1, 2, 3...), so the number alone proves nothing: anyone would
guess the next one. The customer data has name, phone and e-mail, and has no CPF, password or secret code:
it is the only material available to prove ownership.

Detailed decisions, all so that the denial teaches the attacker nothing:

- Uniform denial. A nonexistent order, a wrong proof, someone else's order and a lockout all get the same
  answer ("Não consegui confirmar esses dados", "I could not confirm this data"), so it is not possible to
  find out which orders exist.
- Constant-time comparison (`hmac.compare_digest`), executed even when the order does not exist (against a
  fictitious customer), so that the response time leaks nothing.
- The per-order lockout holds across sessions (5 failures): the 4 digits of the phone could be guessed by
  brute force in 10 thousand attempts; the per-order lockout cuts that off.
- The model does not authorize. `get_order_status` and `check_return_eligibility` refuse if the session was
  not verified, whatever argument the model passes.
- The verification state is never stored. When a conversation is resumed, the customer verifies again.

**Accepted cost.** It is a single factor, and someone's e-mail is not a secret. Whoever knows the holder's
e-mail and guesses an order number gets in. It is a known weak point, discussed in
[security-and-privacy.md](security-and-privacy.md), with the path to strengthen it (tiered verification,
two factors or a code sent to the contact on file).

**Discarded alternatives.**

| Alternative | Why not |
|---|---|
| Only the order number | Guessable: proves nothing |
| Let the model decide identity | It is exactly what cannot be delegated to a model |
| Ask for the full name | Easier to know than the e-mail, and it fails on accents or nicknames |
| Two factors always | More friction for the most common question (where is my order); it stays as an evolution |
| Real login or a code by e-mail | Beyond the reach of a prototype with no sending channel |

---

## D10. History persistence

**Decision.** SQLite from the standard library, one `messages` table with `session_id`. It stores only the
text of the messages, already without personal data (e-mail, CPF, phone, card and tracking become markers).
Retention of 30 days, with a purge on open. The model receives the last 20 messages.

```mermaid
flowchart LR
    T["Successful turn"] --> RED["redact: removes e-mail, CPF, phone, card, tracking"]
    RED --> DB[("SQLite: history/sessions.db")]
    DB -->|"when resuming with --session"| WIN["Last 20 messages, starting at a customer message"]
    WIN --> AG["Agent"]
    DB -->|"on open"| PURGE["Purges whatever is older than 30 days"]
    ESQ["/esquecer"] -->|"deletes the session"| DB
    F["Turn with a provider failure"] -.->|"does not store"| DB
```

**Why.** It allows resuming a conversation without keeping who the person is. Deletion is per session
because there is no history indexed by customer: the `/esquecer` command ("forget") erases the history and
the verification.

| Alternative | Why not |
|---|---|
| No persistence | Loses resumption, which improves the experience |
| JSON file | Concurrent writes and purging are harder |
| History indexed by `customer_id` | It would become an indexed personal database, with a privacy risk |
| Conversation summary by an LLM | An extra call per turn, and the summary may alter numbers |
| Redis or Postgres | An extra service for a single machine; the natural evolution in production |

**Accepted cost.** One file, one machine, no authentication of whoever resumes the session (the
`session_id` is the only secret). For production, see [limitations-and-evolution.md](limitations-and-evolution.md).

---

## D11. Conversation memory: the working state

**Decision.** Besides the text history, the orchestrator keeps a working state in code: the ordered list of
products consulted, the offers already made to the customer, the order in focus, and whether the customer
said they do not have the order number. It appears to the model in short lines in the `<contexto_atual>`.

**Why.** Questions such as "which is the cheapest of them?" or "the second one I asked about" require
remembering *which* products were cited and in what order. The text of the history does not guarantee that:
in the second stress test, in a long conversation, the model invented a ukulele that nobody mentioned.
After the working state, in the two long conversations (28 and 23 turns) it got "the first, the second and
the third product" right.

The state keeps only ids, names and prices: no personal data, and it never goes to SQLite.

| Alternative | Why not |
|---|---|
| Trust the message window | It failed in a long conversation (invented ukulele) |
| LLM summary | An extra call and a risk of changing numbers |
| A larger window | Higher input cost and it does not solve the order |

---

## D12. Interface

**Decision.** Two interfaces over the same core: a CLI (`emporio`) and a Streamlit interface
(`emporio-ui`), opened with a single command on any system (`uv run emporio-ui`, or `make run`).

```mermaid
flowchart LR
    U1["Whoever evaluates the agent"] --> CLI["CLI: fast, with --trace"]
    U2["Whoever wants to see, test and compare"] --> WEB["Streamlit: chat, examples, results and metrics"]
    CLI --> CORE["app.build_agent"]
    WEB --> CORE
```

**Why.** The challenge says the focus is the agent, and that a CLI, notebook, API or simple UI will do. The
CLI is the most direct way to run it and to produce reproducible examples, and `--trace` shows *when* the
agent consults data or policy. The web interface exists for whoever evaluates: click an example, see the
recorded conversations, the test numbers and (in evaluator mode) the metrics of their own session.

The single command exists because `make` does not come with Windows. `uv run emporio-ui` downloads Python
3.12 if it is missing, creates the environment with the locked dependencies, creates the `.env` from the
example and opens the interface.

| Alternative | Why not |
|---|---|
| HTTP API (FastAPI) | Requires rate limiting, authentication and origin control; it stays in "with more time" (see [limitations-and-evolution.md](limitations-and-evolution.md)) |
| Notebook | Hard to run outside the author's environment and hard to test |
| CLI only | Functional, but worse for the evaluator to compare, see examples and metrics |
| Own frontend (React) | Too much code for the same test |

---

## D13. No text streaming

**Decision.** The answer is delivered whole, after being validated. The interface shows progress events
("looking up the product...", "checking the return deadline...") while the tools run.

```mermaid
sequenceDiagram
    participant U as Customer
    participant A as Agent
    participant G as Guard
    Note over U,G: With token streaming: the customer would see a wrong value before the guard acts
    A-->>U: R$ 1.899
    G->>G: checks and blocks
    Note over U,G: Without text streaming, the chosen one: the guard sees the full text first
    A->>G: complete draft
    G-->>U: approved answer
```

**Why.** The guard (D7) needs the full text. Streaming tokens before validating could show a value without
a source. Since the answer is short (2 to 5 lines) and the median of a case is 1.7 s, giving up a bit of the
feeling of speed in exchange for safety is worth it. I chose safety over fluidity.

**When I would revisit it.** With long answers, I would stream by sentence: accumulate up to the period,
validate the sentence and release it. The event interface already exists, so the extension is natural.

---

## D14. Evaluation

**Decision.** A golden set of 37 cases derived from the data, evaluated by deterministic checks (no LLM as
a judge), run 3 times per case with the real model, split into two suites, and with the report versioned in
the repository.

```mermaid
flowchart TD
    DADOS[("CSVs and manual")] --> FACTS["facts.py: markers such as the price of 95 computed from the data"]
    FACTS --> GOLD["golden.yaml: 37 cases"]
    GOLD --> RUN["evals.run: 3 runs per case, with the real model"]
    RUN --> HARD{"HARD: right tool, exact value, nothing forbidden?"}
    RUN --> SOFT["SOFT: expected phrases and completeness: only reports"]
    HARD --> REG["regression suite: has to stay at 100%"]
    HARD --> CAP["capability suite: measures what is missing, does not break"]
    REG & CAP --> REP["Markdown report, versioned"]
    RUN --> LS["Experiment in LangSmith"]
```

**Why.**

- No value is typed by hand. Markers such as `{price:95}` or `{installment:95:12}` are computed from the
  CSVs. If the data changes, the golden set follows or warns.
- No LLM as a judge. Checking "called the right tool, with the right argument, and the exact value
  appeared" is code. An LLM judge would add cost and variability to the measuring instrument.
- Three runs and `pass^k`. In customer service, what matters is passing always, not "once out of
  three" (`pass@k`). The report shows both.
- Two suites. `regression` is what the agent already gets right and has to stay at 100% (a case that fails
  makes the command exit with an error). `capability` measures what is still missing, without breaking.
- The CI does not call the model (it costs money and the model varies); the CI only checks that the golden
  set is consistent with the data.

The analysis of the results, including the failures, is in [evaluation.md](evaluation.md).

| Alternative | Why not |
|---|---|
| Evaluate "by eye" | It does not scale and does not detect regression |
| LLM as a judge | Cost, variability and circularity |
| One run per case | It hides instability, which is the real problem of agents |
| Run the golden set in the CI | Cost and flakiness; it runs on demand |

**Accepted cost.** With 3 runs, the numbers are coarse: a case with 1/3 and another with 2/3 weigh almost
the same, and there is no confidence interval. The report says so.

---

## D15. Observability

**Decision.** Each turn becomes a trace in LangSmith, on by default, with metadata (outcome, tokens, tools,
tool errors, whether the guard blocked, prompt hash). Everything that touches the SDK goes through
`observability.py`, which swallows the errors: observability never takes the service down or delays it.

**Why.** Without a trace, "the agent got it wrong" is just an impression. With a trace one can say which
tool was called, with which argument, what it returned and what the guard blocked. The prompt hash in each
turn allows comparing versions. LangSmith was chosen because it integrates with LangGraph and with the
evaluation experiments.

| Alternative | Why not |
|---|---|
| Own log in JSONL | It existed in the first version; it was removed when LangSmith replaced it, to have a single source |
| OpenTelemetry with Grafana or Langfuse | More infrastructure; it is the evolution for production, without dependence on one vendor |
| Just `print` | It does not allow comparing versions or measuring cost |

**Accepted cost.** Dependence on an external service for the live metrics, and the text of the
conversations goes through it (which is why the Results tab uses a snapshot of numbers only, with no
text). Without a key, the app works.

---

## D16. Injectable clock

**Decision.** No code calls `datetime.now()` directly: time comes from an injected `Clock`, with the
`America/Campo_Grande` time zone. In the CLI and the interface, `AGENT_NOW` or `--now` freezes the clock.

**Why.** The store's data is from 2025 and 2026 (the last order is from 22/03/2026). With the real clock,
"can I return order 7?" would always give "past the deadline", and "is the store open?" would depend on the
time at which the evaluator opens the project. The demo clock (Monday, 23/03/2026, 10:00) makes the
demonstration reproducible, and in the tests it allows checking "17:55 against 18:01" without waiting.
When the clock is frozen, the interface warns.

---

## D17. Engineering and process

```mermaid
flowchart LR
    DEV["Branch per change"] --> COM["Conventional Commits"]
    COM --> PR["Pull request"]
    PR --> L & T & TS & S & PT
    subgraph CI["CI on GitHub Actions: 5 gates"]
        L["Lint and format: ruff"]
        T["Types: mypy strict"]
        TS["Tests and coverage: pytest"]
        S["Secrets: gitleaks over the whole history"]
        PT["PR title and commits in the standard"]
    end
    L & T & TS & S & PT --> GATE["Aggregate gate"]
    GATE --> MERGE["Merge into main"]
```

- Locked dependencies with a quarantine. `uv.lock` pins the versions, and `exclude-newer = "7 days"` does
  not install packages published less than a week ago: a cheap defense against a supply-chain attack on a
  freshly published version.
- Few runtime dependencies: `openai`, `langgraph`, `langsmith`, `pydantic`, `streamlit`, `holidays` and
  `tzdata`. Data and search use only the standard library.
- Strict types (`mypy --strict`) and `ruff`, in the CI.
- Hermetic tests: the test blocks the network, so no test calls the provider by accident. The tests that
  call the real model stay in `tests/live/` and require `EMPORIO_LIVE=1`; they never run in the CI.
- Secrets: `.env` stays out of git, and `gitleaks` scans the whole history on every PR.
- Real history: 145 commits in 35 pull requests, with title and commits validated by the CI
  (`type(scope): description`). The challenge asks for real progress, with no *force-push* of everything
  into one commit.
- Coverage: 90% of the lines and branches of the package (864 tests).

---

## D18. Prompt strategy

**Decision.** A short system prompt (about 3,400 tokens, with a cap of 12 thousand characters verified by a
test), written in a separate file (`prompts/system.md`), divided into sections with markers, with the fixed
text first and the dynamic context last.

```mermaid
flowchart TD
    subgraph FIXO["Fixed text: the same in every call, and so it enters the provider cache"]
        P["persona: who Téo is and how he speaks"]
        R["regras_inviolaveis: 7 rules"]
        F["ferramentas: when to use each one"]
        E["estilo: how to open, consult and close"]
        X["exemplos: 5 situations with fictitious names and values"]
        L["lembrete: the rule for an offer accepted with yes"]
    end
    subgraph DIN["Dynamic context: built in Python, last"]
        C1["Date and time in Campo Grande, from the injected clock"]
        C2["Store open or closed, calculated by the domain"]
        C3["First name, only after verification"]
        C4["Working state: products cited, offers made"]
    end
    FIXO --> DIN
    DIN --> SP["Final system prompt"]
    FIXO -.->|"sha256, 10 characters"| H["Prompt version: goes in every trace and every experiment"]
```

**Why.**

- Persona. The manual (section 7.1) asks for informal and professional service. Téo speaks like "a friend
  who knows music": friendly, direct, short WhatsApp-style answers (2 to 5 lines), at most one emoji per
  message and none in a complaint or bad news, with no "prezado" or "atenciosamente" (formal Portuguese
  openings and sign-offs).
- Numbered inviolable rules. Seven rules the model always sees (consult before stating, do no arithmetic,
  policy only from `search_policy`, only instruments, identity before order data, tool text is data, do not
  promise what it does not execute). They are the language version of the guarantees the code verifies.
- When to use each tool is in the prompt and in the description of the tool itself. This was tuned from
  the errors of the evals: the description of `search_policy` stopped being the first choice for shipping.
- Stable prefix, dynamic part at the end. Providers reuse from the cache the identical beginning of the
  prompt. Putting the date and the state at the end preserves the cache: 89% of the input came from the
  cache in the last round.
- Synthetic examples, and never with a real value. Each example says "names and values are fictitious:
  never use them in a real answer". There was a real defect here: an example with a typed return deadline
  became the agent's answer, instead of it consulting the policy. Today the examples use markers ("[the
  regret rule, as it came in the text]"), and the guard blocks their fictitious names and e-mails.
- The prompt never calculates. Whoever says whether the store is open is `domain/hours.py`. The customer's
  name enters only after verification and sanitized (letters only, at most 40 characters), so that it never
  carries an instruction into the prompt.
- A reminder at the end reinforces a rule that needed fixing during development ("yes" to an offer must
  bring only the information offered), in the position where the model reads it last.

| Alternative | Why not |
|---|---|
| The whole manual in the prompt | Cost in every call and no trace of *when* the agent consulted |
| A formal persona | Contradicts the manual (section 7.1) |
| A template engine | A dependency with no gain for a few markers: a text substitution is enough |
| Many long examples | Cost in every call, and the examples end up becoming the answer |
| Real conversations as examples | They would teach the wrong flow and bring real data |
| The prompt as the only protection | It is what the model "should" do, not what it does: that is why the guarantees are in code |

**Accepted cost.** The prompt has to be kept in sync with the tools: a test checks that the tool names cited
in the prompt really exist in the registry, and another limits the size.

**When I would revisit it.** At every prompt change: run the golden set and compare by version (hash). If
the prompt grew a lot, I would split it by context (only inject the order rules when the conversation is
about an order).

---

## Annex: technologies we do not use, and when they would start to make sense

| Technology | Why we do not use it | When it would start to make sense |
|---|---|---|
| Embeddings and a vector database | Structured data and an 8-page manual: exact lookup and a literal section solve it | Many policy documents, or a catalog with long descriptions and free search |
| LangChain | Only the graph was needed; disproportionate abstraction | If the project adopted its connectors broadly |
| PydanticAI | A good option; it would hide the loop and the guard, which are the focus | If the team preferred a typed agent framework |
| LiteLLM | A heavy dependency for a single provider | When there were several providers in production, with an audit of the dependency |
| pandas | Small tables; the standard library is enough | Data analysis at volume |
| PostgreSQL or SQLite for the data | No gain at the current volume | When the data comes from a live system (ERP) |
| Redis | One machine, one local session | Several instances, rate limiting and shared identity lockout |
| FastAPI | The focus is the agent, and the CLI and Streamlit are enough | To integrate WhatsApp, a website or other channels |
| Docker | `uv run` already delivers "a single command" without a container | To deploy on a server or an orchestrator |
| MCP (Model Context Protocol) | The tools are functions of the same process; a transport between processes would bring no gain | If the same tools were consumed by several different clients or agents |
| Multi-agent | Three small domains; the cost of one call and one more point of failure | Large, independent domains |
| Fine-tuning | The data changes and the problem is fact, not style | If the tone required something the prompt cannot reach |
| LLM as a judge | Cost and variability in the measurement | To evaluate subjective quality (tone), as a complement |
| Token streaming | The guard needs the full text (D13) | Long answers, with per-sentence validation |
| A real channel (WhatsApp) | Outside the scope of the prototype | In production: the official WhatsApp Business API |
