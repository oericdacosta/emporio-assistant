# Architecture

How Téo works inside: the layers, the path of a message, what each module does and where each piece of
data is kept. Every section has a diagram. The *why* behind each choice is in
[technical-decisions.md](technical-decisions.md).

## Contents

1. [The central idea: the model talks, the code decides](#1-the-central-idea-the-model-talks-the-code-decides)
2. [Layered view](#2-layered-view)
3. [The path of a message](#3-the-path-of-a-message)
4. [One turn, step by step (real example)](#4-one-turn-step-by-step-real-example)
5. [The 11 tools](#5-the-11-tools)
6. [The tool contract](#6-the-tool-contract)
7. [The defense layers](#7-the-defense-layers)
8. [State and persistence: what lives where](#8-state-and-persistence-what-lives-where)
9. [The model layer (LLM)](#9-the-model-layer-llm)
10. [Observability](#10-observability)
11. [The interfaces](#11-the-interfaces)
12. [Module map and the dependency rule](#12-module-map-and-the-dependency-rule)

---

## 1. The central idea: the model talks, the code decides

The problem has two natures. Understanding what the customer wants, in informal Portuguese and with
typos, is language: the model does that well. Saying how much something costs, whether the store is open,
whether an order can still be returned or whether someone really owns an order is a rule: it needs a
correct answer, testable and identical in every run.

Téo keeps the two apart. The model chooses the tool, fills in the arguments and writes the answer in the
store's persona. Everything that has a "correct" answer comes from deterministic code.

```mermaid
flowchart LR
    subgraph LLM["The model does (language)"]
        A1["Understand the intent"]
        A2["Choose the tool"]
        A3["Fill in the arguments"]
        A4["Write in Téo's persona"]
    end
    subgraph COD["The code does (rules)"]
        B1["Price, PIX and installments"]
        B2["Opening hours and holidays"]
        B3["Return deadline and warranty"]
        B4["Shipping"]
        B5["Customer identity"]
        B6["Check the answer before sending"]
    end
    A2 --> COD
    COD --> A4
```

A direct consequence: the model never calculates and never decides authorization. When it makes a
mistake, a layer of code notices before the answer reaches the customer (section 7).

---

## 2. Layered view

```mermaid
flowchart TB
    subgraph UI["Interfaces"]
        CLI["CLI: emporio"]
        WEB["Streamlit: emporio-ui"]
    end
    subgraph AG["Agent (src/emporio/agent)"]
        GRAPH["Turn graph (LangGraph)"]
        GUARD["Guards: ledger, filters, gate, intents"]
        ROUTER["Scope router"]
        STATE["Working state"]
    end
    subgraph TL["Tools (src/emporio/tools)"]
        REG["Registry: validates arguments and returns errors as data"]
        T1["catalog"]
        T2["orders"]
        T3["policy"]
        T4["store"]
        T5["complaints"]
    end
    subgraph DM["Domain and data"]
        DOM["domain: pure rules with Decimal"]
        DAT["data: CSVs, search and policy manual"]
        ID["identity: verification and lockout"]
    end
    subgraph TR["Cross-cutting"]
        LLMC["llm: OpenAI client, replay"]
        SES["Session in SQLite"]
        OBS["Observability: LangSmith"]
        PRV["Privacy: personal data redaction"]
    end
    CLI --> GRAPH
    WEB --> GRAPH
    GRAPH --> ROUTER
    GRAPH --> GUARD
    GRAPH --> STATE
    GRAPH --> LLMC
    GRAPH --> REG
    REG --> T1 & T2 & T3 & T4 & T5
    T1 & T2 & T3 & T4 & T5 --> DOM
    T1 & T2 & T3 & T4 --> DAT
    T2 & T5 --> ID
    CLI --> SES
    WEB --> SES
    GRAPH -.-> OBS
    SES --> PRV
```

| Layer | Folder | Responsibility |
|---|---|---|
| Interfaces | `cli/`, `web/` | Receive the message, show the answer. They have no business rules. |
| Agent | `agent/` | Orchestrate the turn: route, call the model, run tools, check and close the answer. |
| Tools | `tools/` | The model's only window to the world. They validate arguments and return data, never exceptions. |
| Domain | `domain/` | Pure business rules: price, opening hours, returns, shipping, availability. No network, no I/O. |
| Data | `data/` | Loading the CSVs into immutable objects, product search, policy manual by section. |
| Identity | `identity.py` | Verify the owner of the order and lock out attempts. |
| Model | `llm/` | The `LLMClient` contract, the OpenAI client and conversation recording/replay. |
| Session | `agent/session.py` | History in SQLite, with no personal data. |

---

## 3. The path of a message

Each customer message becomes a turn. The turn is a LangGraph graph (`agent/graph.py`): nodes that
transform a state and edges that choose the next step.

```mermaid
flowchart TD
    START(["Customer message"]) --> R["router: injection, out of scope or emergency?"]
    R -->|"yes: canned answer, no model"| FIM(["Answer"])
    R -->|"no"| RF["resume_flows: resume verification and complaint"]
    RF --> M["model: call the LLM with the tools"]
    M -->|"asked for tools"| T["tools: run and record in the ledger"]
    T --> M
    M -->|"final text"| G{"guard: does the answer hold up?"}
    G -->|"yes"| C["compose: remove the repeated offer"]
    C --> FIM
    G -->|"no, 1st time"| M
    G -->|"no, again, or no steps left"| FB["fallback: safe answer"]
    FB --> FIM
```

What each node does:

| Node | What it does | Why it exists |
|---|---|---|
| `router` | Deterministic rules over the message: prompt injection, out-of-scope request, health emergency. If one matches, it answers without calling the model. | Cheaper, faster and does not depend on the model obeying the prompt. |
| `resume_flows` | If the message carries the proof of identity and there is a pending order, it runs `verify_customer` in code. If it carries a contact for a complaint already described, it runs `register_complaint`. | The model does not do this reliably when the customer gave everything in a single message. |
| `model` | One call to the LLM with the system prompt and the 11 tools. On the last allowed iteration, the call goes without tools. | A hard limit against infinite loops. |
| `tools` | Runs up to 3 calls per step, through the registry, and stores the results in the `Ledger`. | The model's only path to the data. |
| `guard` | Checks the answer (section 7). | Nothing reaches the customer without passing here. |
| `compose` | Removes the closing hook the customer has already received (for example, offering installments again). | Avoids a repetitive answer. |
| `fallback` | Builds a safe answer with what the tools returned, or the message "I don't have that confirmed". | The last net: the customer never receives a value without a source. |

Turn limits (`AgentConfig`): at most 5 model iterations (the last one without tools), 3 tool calls per
step, 600 output tokens, temperature 0.2 (0.0 in the evals).

---

## 4. One turn, step by step (real example)

Conversation recorded in [examples/conversations/03-devolucao-fora-do-prazo.md](../examples/conversations/03-devolucao-fora-do-prazo.md),
second turn: the customer gives the order, the date and the e-mail in a single message.

```mermaid
sequenceDiagram
    actor C as Customer
    participant RT as router
    participant RF as resume_flows
    participant M as Model (LLM)
    participant TL as Tools
    participant DM as Domain and data
    participant G as Guard

    C->>RT: "É o pedido 7, recebi dia 17/02. Meu e-mail é leticia.rocha@jmail.com"
    RT-->>RF: nothing to block, continue
    RF->>TL: verify_customer(order_id=7, proof=e-mail)
    TL->>DM: check the proof against the owner of the order
    DM-->>TL: verified, first name "Letícia"
    TL-->>RF: result
    RF-->>M: history + call/result pair
    M->>TL: check_return_eligibility(order_id=7, reason=regret, received_on=2026-02-17)
    TL->>DM: 7 calendar days counted from receipt
    DM-->>TL: verdict: past the deadline, expired on 24/02/2026
    TL-->>M: result
    M-->>G: draft: "Letícia, o prazo ... venceu em 24/02/2026 ..."
    G->>G: is every date and value in the ledger? filters ok?
    G-->>C: approved answer
```

The code verified the customer and calculated the deadline. The model only understood that it was a
regret return (`reason=regret`), called the right tool and wrote the answer with the verdict already given.

---

## 5. The 11 tools

The model only sees the world through these functions. All are read-only, except `register_complaint`,
which writes a local record.

```mermaid
flowchart LR
    MODEL(["Model"]) --> CAT & PED & POL & LOJ & REC
    subgraph CAT["Catalog"]
        SP["search_products"]
        GP["get_product"]
        CPO["calculate_payment_options"]
        CCT["calculate_cart_total"]
    end
    subgraph LOJ["Store"]
        GSI["get_store_info"]
        ES["estimate_shipping"]
    end
    subgraph POL["Policy"]
        SPOL["search_policy"]
    end
    subgraph PED["Orders (require identity)"]
        VC["verify_customer"]
        GOS["get_order_status"]
        CRE["check_return_eligibility"]
    end
    subgraph REC["Customer service"]
        RC["register_complaint"]
    end
```

| Tool | When to use | What it returns |
|---|---|---|
| `search_products` | Options by category, price range, brand or name | Up to 8 products with price, `total_matches`, `not_shown`, ambiguous candidates and whether each is for sale |
| `get_product` | Price, promotion, PIX, availability and specs of one product | A ready sheet, with list price, promotion and PIX only when they apply |
| `calculate_payment_options` | PIX, debit, boleto and installments | Installment plan already calculated, respecting the minimum per installment |
| `calculate_cart_total` | Several items at once | Total, PIX and installments of the cart |
| `estimate_shipping` | Shipping cost and deadline by city | Cost and deadline in Campo Grande; modalities and quote in other cities; refuses destinations outside Brazil |
| `get_store_info` | Address, phone, WhatsApp, opening hours, "is it open now?" | Fixed data and the status computed from the clock |
| `search_policy` | Rules for exchange, warranty, payment, shipping, promotions, privacy | Up to 3 literal sections of the manual, with the decision notes and the coverage of the question |
| `verify_customer` | Before any order data | `verified` and the first name, or a uniform denial |
| `get_order_status` | Status, items, tracking and delivery estimate | Order data, only for the verified customer |
| `check_return_eligibility` | Return, exchange or warranty of an order | Verdict calculated in code (`eligible`, `not_eligible`, `legal_warranty`, `escalate`...) |
| `register_complaint` | Complaint, exception, policy question | Protocol `EMP-XXXXXXXX` and a return time of 24 business hours |

The description of each tool says when to use it and when not to. This was tuned from the real errors of
the evals (for example, the description of `search_policy` stopped being the first choice for shipping).

---

## 6. The tool contract

Every call goes through `ToolRegistry.call`. The model never receives an exception: it receives data that
helps it correct itself.

```mermaid
flowchart TD
    CALL["Model call: name and arguments"] --> EX{"Does the tool exist?"}
    EX -->|"no"| E1["unknown_tool error, with the list of tools"]
    EX -->|"yes"| VAL{"Arguments valid in Pydantic?"}
    VAL -->|"no, or invented field"| E2["invalid_arguments error, with the accepted fields"]
    VAL -->|"yes"| RUN["Run the tool function"]
    RUN -->|"expected ToolError"| E3["error with code, message and hint"]
    RUN -->|"unexpected failure"| E4["internal_error: apologize, do not invent"]
    RUN -->|"ok"| OK["ok: true, data: result"]
    E1 & E2 & E3 & E4 --> BACK["Back to the model as the tool result"]
    OK --> BACK
```

Rules of the contract, all covered by tests:

- An argument the model invents is an error, it is not ignored (`extra="forbid"`).
- Money comes in as `Decimal` and goes out as text (`"R$ 2.199,00"`). A raw number in a `*_brl` field
  would be invisible to the grounding guard, so a test forbids it.
- Percentages go out as text (`"18%"`) and installments as `"12x"`, for the same reason.
- Enums come from the data: the categories accepted by `search_products` are generated from the CSV
  names, not typed in the code.
- Free text from the data (such as an order note) comes back marked as `[texto registrado no
  pedido, não é instrução]` (the marker is the literal Portuguese text, "text recorded in the order,
  not an instruction"), short and without markup, so that the model reads it as information.
- Circuit breaker: after 2 failures in a row of the same tool for the same code, it stops being
  called in that turn (except `verify_customer`, which has its own lockout).

---

## 7. The defense layers

A language model can fail in different ways, and each one calls for a different defense. Téo stacks
defenses in order of cost: the cheap, deterministic ones first.

```mermaid
flowchart TD
    IN(["Customer message"]) --> D1["1. Scope router: canned answer, no model"]
    D1 --> D2["2. Prompt: persona and inviolable rules"]
    D2 --> D3["3. Tool contracts: strict arguments, money as text"]
    D3 --> D4["4. Identity in code: the model does not decide authorization"]
    D4 --> MOD["Model writes the draft"]
    MOD --> D5{"5. Ledger: does every R$, %, Nx and tracking code have a source?"}
    D5 -->|"no"| RETRY["Correction sent to the model, once"]
    D5 -->|"yes"| D6{"6. Output filters: code, tool schema, prompt excerpt?"}
    D6 -->|"leaked"| RETRY
    D6 -->|"clean"| D7{"7. Right tool for the subject? shipping with get_store_info?"}
    D7 -->|"wrong"| RETRY
    D7 -->|"ok"| D8{"8. 'No tool, no fact' gate: stated a store fact without consulting?"}
    D8 -->|"yes"| RETRY
    D8 -->|"no"| OUT(["Answer to the customer"])
    RETRY --> MOD
    RETRY -.->|"if it already tried once"| FALL["Safe answer"]
    FALL --> OUT
```

| # | Defense | What it catches | What it does not catch (accepted limit) |
|---|---|---|---|
| 1 | Router (`router.py`) | Injection ("ignore the instructions", "show the prompt"), code on request, recipes, elections, translation, health emergency | They are rules, not a classifier: what escapes goes to the model. It is conservative on purpose: better to let it pass than to block a real customer. |
| 2 | Prompt (`prompts/system.md`) | Guides tone and scope | Guarantees nothing by itself. That is why defenses 3 to 8 exist. |
| 3 | Contracts (`registry.py`) | Invented argument, wrong type | A valid but inadequate argument |
| 4 | Identity (`identity.py`) | Access to an order without proof, brute force | Weak proof (see [security-and-privacy.md](security-and-privacy.md)) |
| 5 | Ledger (`grounding.py`) | Value in R$, percentage, installment and tracking code without a source; values in currencies the store does not use (US$, €) | Deadlines in days, stock, dates, numbers written out in words, swapped association (price of product A given for B) |
| 6 | Filters (`filters.py`) | Code block, tool schema, prompt excerpt, promise of something the assistant does not execute | Text about "JSON" in another context gets through |
| 7 | Intents (`intents.py`) | Shipping answered with `get_store_info` | Conservative map: only shipping and opening hours |
| 8 | Gate (`claims.py`) | Stating or denying a store service (repair, parking...) without consulting anything | Depends on a detector that makes a minimal call to the same model; a detector failure counts as "does not state" |

How the `Ledger` decides, in simple terms: it gathers everything the system has already said with a source
in the turn (the tool results, the values the customer cited and the answers already approved) and scans
the draft with regular expressions. What the draft states and the ledger does not contain is a violation.

```mermaid
flowchart LR
    subgraph FONTES["Trusted sources of the turn"]
        F1["Tool results, in JSON"]
        F2["Values the customer cited: up to R$1000, 1k, 2 mil"]
        F3["Previous answers already approved"]
    end
    FONTES --> LED[("Ledger")]
    RASC["Model draft"] --> VAR["Scan: R$, reais, %, Nx, tracking, protocol"]
    VAR --> CMP{"Is each value in the ledger?"}
    LED --> CMP
    CMP -->|"yes"| PASS["Approved"]
    CMP -->|"no"| VIOL["Violation: back to the model with the correction"]
```

---

## 8. State and persistence: what lives where

A privacy decision of the project: personal data and security state never go to disk.

```mermaid
flowchart TB
    subgraph MEM["Memory only (gone on exit)"]
        WS["Working state: products asked about, in order; offers made; order in focus"]
        SG["SessionGuard: verified customer, failures, lockout, protocols"]
        IV["IdentityVerifier: failures per order, shared across sessions"]
        LG["Ledger of the turn"]
    end
    subgraph SQL["SQLite: history/sessions.db"]
        MSG["messages: id, session_id, role, content, created_at"]
    end
    subgraph JSONL["logs/complaints.jsonl"]
        CP["complaints: protocol, summary, contact"]
    end
    subgraph LS["LangSmith (optional)"]
        TR["turn traces, masked on display"]
    end
    U(["Successful turn"]) -->|"text already without e-mail, CPF, phone, card, tracking"| MSG
    U --> TR
    WS -. "never writes" .-> SQL
    SG -. "never writes" .-> SQL
```

| Data | Where | Retention | Note |
|---|---|---|---|
| Text of the messages (customer and Téo) | SQLite | 30 days, purged on open | Goes through `redact`: e-mail, CPF, phone, card and tracking code become markers |
| Who was verified, failures, lockout | Memory | Session lifetime | When a conversation is resumed, the customer verifies again |
| Working state (products cited etc.) | Memory | Session lifetime | Only ids, names and prices. Never personal data |
| Tool results | Memory and LangSmith | Turn lifetime | They do not go to SQLite |
| Complaints | `logs/complaints.jsonl` | Local | The forwarding to management is simulated |
| Window sent to the model | Memory | Per call | The last 20 messages, starting at a customer message |

A turn is only saved if it ended well. A provider failure leaves the session as it was, and the customer
can resend the same message.

The working state (`agent/state.py`) exists because the text history is not enough for the model to
answer "the cheapest of them?" or "the second one I asked about". The orchestrator keeps, in code, the
ordered list of products consulted, the offers already made, the order in focus and whether the customer
said they do not have the order number, and shows this to the model in short lines inside the
`<contexto_atual>` ("current context") block of the prompt.

---

## 9. The model layer (LLM)

The agent only knows the `LLMClient` contract (`llm/base.py`): a `chat(messages, tools, ...)` function.
Whoever implements it is a replaceable detail.

```mermaid
classDiagram
    class LLMClient {
        <<Protocol>>
        +str model
        +chat(messages, tools, tool_choice, temperature, max_tokens) LLMResponse
    }
    class OpenAIClient {
        +timeout 30s
        +max_retries 2
        +translated errors
    }
    class ReplayLLM {
        +replays recorded answers
        +fails if the prompt changes
    }
    class FakeLLM {
        +scripted in the tests
    }
    class Agent
    LLMClient <|.. OpenAIClient
    LLMClient <|.. ReplayLLM
    LLMClient <|.. FakeLLM
    Agent --> LLMClient : uses
```

- `OpenAIClient`: translates the contract to the OpenAI chat API and converts the SDK errors into two
  families (`ProviderUnavailable` for a passing failure or a usage limit, `ProviderFatal` for an invalid
  key or model); a missing model or key is a `ConfigError`, detected before the first call. It also wraps
  the client with LangSmith (a no-op when tracing is off).
- No default model in the code. `LLM_MODEL` is required: model names age, and a clear configuration error
  is better than a "model does not exist" on the first message. A test guarantees that no model name
  appears in `src/`.
- Prompt with a stable prefix. The fixed text of the prompt comes first and is the same on every call, and
  the dynamic part (`<contexto_atual>`: date and time in Campo Grande, store open or closed, first name of
  the verified customer, working state) comes last. This enables the provider's prompt cache: in the
  evals, about 90% of the input tokens came from the cache (89% in the last golden round).
- Replay. `ReplayLLM` records and replays only the model's answers; tools, clock and guard run for real.
  If what the model would see has changed, the replay fails, instead of showing a conversation the system
  no longer produces.
- Provider failures become friendly messages to the customer (`agent/failures.py`), without taking the
  session down.

---

## 10. Observability

Every turn becomes a trace in LangSmith, and none of this can take down or delay the service: everything
that touches the SDK goes through `observability.py`, which swallows the errors.

```mermaid
flowchart LR
    T["Agent turn"] -->|"@traceable"| LS[("LangSmith: project emporio-teo")]
    subgraph META["Metadata of each turn"]
        M1["outcome: answered, corrected or fallback"]
        M2["tokens: input, output and cache"]
        M3["tools used and tool errors"]
        M4["did the guard block?"]
        M5["prompt hash"]
    end
    T --> META --> LS
    LS --> EXP["Eval experiments"]
    LS --> MET["Metrics tab, in evaluator mode"]
    LS --> SNAP["scripts/export_metrics.py produces metrics_snapshot.json"]
    SNAP --> RES["Results tab: no key and no network"]
```

- On by default; without `LANGSMITH_API_KEY` the app works and only warns.
- No mask in the trace on purpose: whoever debugs needs to see the real argument. The mask (`privacy.mask`)
  only applies to showing details on screen.
- Prompt version: a 10-character hash of the fixed text. Each turn and each evaluation experiment carry
  this hash, so metrics can be compared across prompt versions.
- Customer feedback: the web interface has 👍/👎 per answer, recorded on the run of the turn.
- What the two tabs show is in [evaluation.md](evaluation.md#6-the-results-and-metrics-tabs).

---

## 11. The interfaces

```mermaid
flowchart TB
    subgraph CLI["CLI (emporio)"]
        C1["Interactive conversation, or -m for a single message"]
        C2["/ajuda /novo /esquecer /trace /sair (the commands are in Portuguese)"]
        C3["--trace shows queries, tokens and latency"]
        C4["--check-data validates CSVs and manual"]
    end
    subgraph WEB["Streamlit (emporio-ui)"]
        W1["Chat tab: chat, clickable examples, 👍/👎"]
        W2["Examples tab: 5 recorded conversations"]
        W3["Results tab: the author's numbers"]
        W4["Metrics tab: your session, in evaluator mode"]
    end
    CLI --> CORE["build_agent: the same wiring"]
    WEB --> CORE
    CORE --> AGENT["Agent (graph)"]
```

Both interfaces assemble the agent through the same function (`app.build_agent`), so there is no "UI
behavior" different from the CLI's. The logic of the web interface lives in `web/logic.py`, without
Streamlit, and is testable without a browser; `web/app.py` only draws.

The CLI has distinct exit codes (ok, unexpected error, invalid usage, configuration, provider
unavailable, cassette, Ctrl-C), which makes it usable in scripts.

---

## 12. Module map and the dependency rule

```mermaid
flowchart TD
    cli["cli/"] --> app["app.py"]
    web["web/"] --> app
    app --> agent["agent/"]
    app --> tools["tools/"]
    tools --> domain["domain/"]
    tools --> data["data/"]
    tools --> identity["identity.py"]
    data --> domain
    agent --> llm["llm/"]
    agent --> domain
    agent --> privacy["privacy.py"]
    web --> metrics["web/metrics.py"]
```

The rule that holds the design together is "the agent does not know the concrete tools": the `agent/`
package never imports `emporio.tools`. What joins the two is `app.py`, the layer above. An architecture
test (`tests/unit/agent/test_architecture.py`) reads the source code with `ast` and fails if anyone
breaks this.

The gain: the agent engine (the graph) can be replaced, or the tools can be reused by another agent,
without touching the other side. That is what allowed replacing the hand-written loop with LangGraph
without touching the tools and the domain (see
[technical-decisions.md](technical-decisions.md#d3-turn-orchestration-from-a-hand-written-loop-to-langgraph)).
