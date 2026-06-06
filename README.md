# ResolveMate — Autonomous Web Resolution Agent 

## Problem Statement

**ResolveMate** is an autonomous agent that resolves consumer issues on a user's
behalf. A user declares a desired outcome — *"get a full refund for order #4821;
it arrived broken"* — plus a **Resolution Policy** (acceptance thresholds, hard
"never" rules, an escalation budget). The agent then:

- **Plans** a strategy from the goal and policy.
- **States the case** to a support channel.
- **Converses, waits, and persists** — interpreting each reply, countering
  lowball offers, escalating around deflection and retention dark-patterns, and
  pausing for human approval at a real guardrail —

…until the issue is **resolved**, **denied**, or its **escalation budget runs
out**.

You are given a **working foundation**. You implement the agent's **decision
core** — the deterministic *negotiator* and the durable *orchestrator* loop that
drives it. The LLM-facing pieces (strategist, classifier) are already built, so
you can focus on the logic that makes the agent correct and trustworthy.

---

## Technology Stack

| Layer | Choice |
|---|---|
| **Language** | Python 3.8+ — **standard library only** (no third-party deps in the foundation) |
| **Web / API server** | stdlib `http.server` (`ThreadingHTTPServer`), JSON over HTTP |
| **Persistence** | SQLite (`sqlite3`) — one row per Case, stored as a JSON blob; survives restarts |
| **LLM client** | Provider-agnostic, built on `urllib`. Works with Azure OpenAI / Azure AI Foundry, OpenAI, Anthropic Claude, Groq, DeepSeek, and Ollama (local) |
| **Frontend** | Single-file vanilla HTML/JS dashboard (`web/index.html`) that polls the API |

---

## Architecture

```
                           ┌─────────────────────────────────────────────┐
   Browser (web/index.html)│  Dashboard: create case, live timeline,      │
   ──────────────────────► │  approve / reject (polls the API every 1.5s) │
                           └───────────────┬─────────────────────────────┘
                                           │ HTTP (JSON)
                           ┌───────────────▼─────────────────────────────┐
   server.py (stdlib http) │  GET/POST /api/cases   ·   run_agent(case_id)│  ← spawns a
   ──────────────────────► │  serves UI + storage-backed API              │    background thread
                           └───────────────┬─────────────────────────────┘
                                           │ calls
                  ┌────────────────────────▼───────────────────────────────┐
                  │  advocate/agent/orchestrator.py   (THE LOOP)            │
                  │                                                          │
                  │   plan ─► open ─► [ receive ─► classify ─► decide ─►     │
                  │                     act ─► persist ] ─► terminal/pause   │
                  └───┬──────────────┬──────────────┬─────────────┬─────────┘
                      │              │              │             │
              ┌───────▼──┐   ┌───────▼─────┐  ┌─────▼──────┐ ┌────▼────────────┐
              │strategist│   │ classifier  │  │ negotiator │ │   Channel       │
              │  (LLM)   │   │   (LLM)     │  │  (CODE —   │ │  (send/receive) │
              │ →CasePlan│   │→Classification│ │ no LLM!)  │ │ Mock | Web | …  │
              │ PROVIDED │   │ PROVIDED    │  │ deterministic│ │ provided       │
              └────┬─────┘   └──────┬──────┘  │ →Decision  │ └────┬────────────┘
                   │                │         └─────┬──────┘      │
                   └────────────────┴───────────────┴────────────┘
                                    │ persist after every step
                           ┌────────▼─────────┐      ┌──────────────────┐
                           │ store.py (SQLite)│      │  llm.py (provider│
                           │  Case JSON blobs │      │  -agnostic chat) │
                           └──────────────────┘      └──────────────────┘
```

**Key design seam:** the LLM *understands and phrases* (strategist, classifier),
but the **consequential decision** — accept money? cross a guardrail? give up? —
is made by **deterministic code** in `negotiator.py`. A model mistake can never
silently approve a payment. Preserving this seam is what makes the agent
trustworthy, and reviewers will look for it.

---

## Data Flow

**Status state machine:**

```
NEW ─► PLANNED ─► OPEN ─►(reply)─► [ negotiating ] ──► RESOLVED
                          │                       └──► DENIED
                          ├──► NEEDS_APPROVAL ─(human)─┤      (escalation budget
                          └──► WAITING (parked) ───────┘       exhausted) ─► ABANDONED
```

**Step by step:**

1. The user fills the dashboard form and submits → `POST /api/cases` stores the
   Case and spawns `run_agent(case_id)` on a background thread.
2. The orchestrator **plans** (`strategist.make_plan` → `CasePlan`), **opens**
   the channel, and sends the opening statement.
3. Each turn: **receive** a reply → **classify** it (`classifier.classify` →
   `Classification`) → **decide** the next action (`negotiator.decide` →
   `Decision`) → **act** (send a message / update status) → **persist**
   (`store.save`).
4. The dashboard polls every ~1.5 s and animates the timeline as the Case
   advances.
5. At a guardrail the agent sets `NEEDS_APPROVAL` and pauses. The human clicks
   approve / reject → `POST /api/cases/<id>/approve` records the decision and
   **resumes** the agent.
6. The loop ends at a terminal state: `RESOLVED`, `DENIED`, or `ABANDONED`.

---

## Project Structure

```
advocate-candidate/
├── server.py                     PROVIDED     web server shell + run_agent() hook
├── web/index.html                PROVIDED     dashboard (case list, timeline, approve)
├── examples/                     PROVIDED     sample cases (refund, subscription cancel)
├── advocate/
│   ├── llm.py                    PROVIDED     provider-agnostic LLM client
│   ├── models.py                 PROVIDED     Case / Policy / Plan / Classification / Decision
│   ├── store.py                  PROVIDED     SQLite Case store (save/get/list)
│   ├── channels/
│   │   ├── base.py               PROVIDED     Channel interface (send/receive)
│   │   ├── mock.py               PROVIDED     simulated adversarial support rep
│   │   ├── web_playwright.py     TODO          real browser channel (optional stretch)
│   │   └── email_channel.py      TODO          real email channel (optional stretch)
│   └── agent/
│       ├── strategist.py         PROVIDED     plan a CasePlan from goal+policy (LLM)
│       ├── classifier.py         PROVIDED     interpret one reply → Classification (LLM)
│       ├── negotiator.py         IMPLEMENTED   deterministic policy decisions
│       └── orchestrator.py       IMPLEMENTED   durable resolution loop
└── requirements.txt              PROVIDED     (foundation = stdlib only)
```

---

## Setup and Run

**Requirements:** Python 3.8+. The foundation has **zero third-party
dependencies** (stdlib `http.server`, `sqlite3`, `urllib`). A working prototype
LLM key is pre-filled in `.env` (shared quota — replace with your own for heavy
use).

```bash
# 1. (optional) point at your own LLM — see ".env" / ".env.example"
cp .env.example .env        # then edit, or use the provided .env as-is

# 2. run the foundation
python3 server.py           # → http://localhost:8000
```

Open the dashboard, click **"refund example"** or **"cancel example"**, and
**Start Case**. The server persists the case, starts the background agent, and
the timeline animates as ResolveMate plans, opens the mock support channel,
classifies replies, decides under policy, and acts.

For the Microsoft-product requirement, configure Azure AI Foundry / Azure
OpenAI in `.env`:

```bash
ADVOCATE_LLM_PROVIDER=azure_openai
ADVOCATE_LLM_BASE_URL=https://YOUR-RESOURCE.openai.azure.com
ADVOCATE_LLM_API_KEY=your-azure-openai-key
ADVOCATE_LLM_MODEL=your-deployment-name
ADVOCATE_AZURE_OPENAI_API_VERSION=2024-02-15-preview
```

---

## Deploy on Render

This repo includes a Render Blueprint in `render.yaml`.

1. Push the project to GitHub.
2. In Render, choose **New** → **Blueprint**.
3. Select this repository.
4. Render will read `render.yaml` and create a web service named
   `resolvemate`.
5. Add the secret value for:

```bash
ADVOCATE_LLM_API_KEY=your-gemini-api-key
```

The Blueprint sets:

```bash
ADVOCATE_LLM_PROVIDER=gemini
ADVOCATE_LLM_MODEL=gemini-2.5-flash-lite
ADVOCATE_DB=advocate.db
```

`server.py` automatically binds to Render's `PORT` environment variable, so no
manual port configuration is needed.

On Render's free plan, the local SQLite file is fine for demos but should be
treated as ephemeral. For production persistence, use a paid Render disk or move
case storage to a hosted database.

For a demo without consuming LLM quota, choose **Scripted (offline demo)** or
**Manual support (type replies)** in the dashboard.

---

## Dataset Files

The "dataset" is a set of **sample input Cases** in `examples/` — each a JSON
spec you can load from the dashboard (the *example* buttons), `POST` to
`/api/cases`, or copy as a template for your own:

| File | Goal type | Highlights |
|---|---|---|
| `examples/refund_case.json` | **Recover money** | Full ₹3,200 refund; `store_credit` forbidden; pause-for-approval below threshold. |
| `examples/subscription_cancel_case.json` | **Get a confirmation** | Cancel a subscription; `target_amount = 0`; retention discounts must be treated as dark-patterns and escalated. |

You are not limited to these — any consumer-support goal works (refund, partial
refund, replacement, cancellation, billing dispute, warranty claim). See the
schema below for the exact fields.

---

## What is Already Implemented

| Component | File | What it does |
|---|---|---|
| **LLM client** | `advocate/llm.py` | Provider-agnostic chat client. `chat()` → text, `chat_json()` → dict. |
| **Data models** | `advocate/models.py` | `Case`, `ResolutionPolicy`, `CasePlan`, `Classification`, `Decision`, and the status/message/action/outcome enums. |
| **Persistence** | `advocate/store.py` | SQLite store: `save` / `get` / `list` / `delete`. Survives restarts. |
| **Channel interface** | `advocate/channels/base.py` | The `send` / `receive` / `open_case` boundary the loop talks to. |
| **Simulated counterparty** | `advocate/channels/mock.py` | An LLM role-plays a stubborn support rep (deflect → lowball → settle). Build the whole loop with no real website. |
| **Strategist** (LLM) | `advocate/agent/strategist.py` | `make_plan(case, llm)` → `CasePlan`. Defensive parsing + offline fallback. |
| **Classifier** (LLM) | `advocate/agent/classifier.py` | `classify(case, reply, llm)` → `Classification`. Strict enum coercion + retention-pattern backstop. |
| **Negotiator** (code) | `advocate/agent/negotiator.py` | Deterministic policy engine. Never accepts forbidden outcomes or below-threshold offers. |
| **Orchestrator** | `advocate/agent/orchestrator.py` | Runs/resumes the plan → receive → classify → decide → act → persist loop. |
| **Web dashboard** | `web/index.html` | Case list, live timeline, approve / reject. |
| **Server** | `server.py` | Serves UI + storage API; starts/resumes the agent on background threads. |

---

## What Can Still Be Extended

The core demo loop is implemented. The main stretch items are real-world
channels and production durability:

> Stretch (optional, not required): build a **real channel** —
> `channels/web_playwright.py` (browser) or `channels/email_channel.py` (IMAP/SMTP)
> — behind the same `Channel` interface; the loop works unchanged.

---

## How the LLM Connector Works

Everything talks to the model through one small class — `LLMClient` in
`advocate/llm.py` — so the rest of the code never cares which provider answered:

```python
from advocate.llm import LLMClient

llm = LLMClient()
text = llm.chat([{"role": "user", "content": "Hello"}])              # -> str
data = llm.chat_json([{"role": "user", "content": "Return JSON ..."}])  # -> dict
```

- **`chat(messages)`** returns the assistant's reply as a string.
- **`chat_json(messages)`** asks for JSON and best-effort parses/repairs it into a
  `dict` (strips ``` fences, extracts the `{...}` object).
- Transport is plain `urllib` with retries on `429`/`5xx`. No SDKs to install.

**Switching providers** is usually an `.env` change only:

```bash
# OpenAI (default)
ADVOCATE_LLM_PROVIDER=openai     ADVOCATE_LLM_API_KEY=sk-...      ADVOCATE_LLM_MODEL=gpt-4o-mini
# Groq            (OpenAI-compatible)
ADVOCATE_LLM_PROVIDER=groq       ADVOCATE_LLM_API_KEY=gsk_...     ADVOCATE_LLM_MODEL=llama-3.3-70b-versatile
# DeepSeek        (OpenAI-compatible)
ADVOCATE_LLM_PROVIDER=deepseek   ADVOCATE_LLM_API_KEY=sk-...      ADVOCATE_LLM_MODEL=deepseek-chat
# Ollama (local, free; run `ollama serve` first; no key)
ADVOCATE_LLM_PROVIDER=ollama     ADVOCATE_LLM_MODEL=llama3.1
# Anthropic Claude (adapter already built in)
ADVOCATE_LLM_PROVIDER=anthropic  ADVOCATE_LLM_API_KEY=sk-ant-...  ADVOCATE_LLM_MODEL=claude-sonnet-4-6
```

- **OpenAI-compatible providers** (Groq, DeepSeek, Together, Ollama, vLLM): env
  vars only.
- **Anthropic Claude**: supported via the built-in adapter.
- **A brand-new provider**: add one entry to the `PROVIDERS` dict in `llm.py`.

---

## Dataset Schema Reference

A **Case** is the unit of work. Inbound fields you supply (the rest are managed
by the agent at runtime):

| Field | Type | Meaning |
|---|---|---|
| `goal` | string | The desired outcome, in plain language. **Required.** |
| `context` | object | Free-form facts (order id, account email, amounts, dates). |
| `evidence` | string[] | Filenames / descriptions of supporting evidence. |
| `policy` | object | The `ResolutionPolicy` (below) — the agent's mandate. |

**ResolutionPolicy** — the guardrails the negotiator never crosses:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `currency` | string | `"INR"` | Currency label for amounts. |
| `min_acceptable_amount` | number | `0` | Auto-accept an acceptable-kind offer at or above this. |
| `target_amount` | number | `0` | What counter-offers aim for. `0` ⇒ a non-money / cancellation goal. |
| `forbidden_outcomes` | string[] | `[]` | Outcome kinds to **never** accept (e.g. `["store_credit"]`). |
| `ask_below_threshold` | bool | `true` | Below threshold: `true` → ask the user; `false` → auto-counter. |
| `escalation_budget` | int | `2` | How many times the agent may escalate before pausing / giving up. |
| `notes` | string | `""` | Extra instructions surfaced to the LLM agents. |

**Runtime fields** (set by the agent, returned by the API): `case_id`, `status`,
`plan`, `transcript`, `escalation_count`, `outcome_kind`, `outcome_amount`,
`created_at`, `updated_at`.

**Enums:**

| Enum | Values |
|---|---|
| `CaseStatus` | `new`, `planned`, `open`, `waiting`, `needs_approval`, `resolved`, `denied`, `abandoned` |
| `MessageType` | `offer`, `info_request`, `deflection`, `denial`, `final_resolution`, `unknown` |
| `ActionType` | `provide_info`, `counter_offer`, `accept`, `escalate`, `pause_for_approval`, `mark_resolved`, `mark_denied`, `abandon` |
| `OutcomeKind` | `refund`, `replacement`, `store_credit`, `apology`, `none` |

**Example Case (the refund sample):**

```json
{
  "goal": "Get a full refund for order #4821 (a ceramic dinner set, ₹3,200) — it arrived cracked. I want my money back, not a replacement or store credit.",
  "context": { "order_id": "#4821", "order_amount": 3200, "account_email": "buyer@example.com" },
  "evidence": ["photo_cracked_plate_1.jpg", "unboxing_video.mp4", "order_invoice.pdf"],
  "policy": {
    "currency": "INR",
    "min_acceptable_amount": 3200,
    "target_amount": 3200,
    "forbidden_outcomes": ["store_credit"],
    "ask_below_threshold": true,
    "escalation_budget": 2,
    "notes": "Customer paid by card and wants the refund to the original payment method."
  }
}
```
