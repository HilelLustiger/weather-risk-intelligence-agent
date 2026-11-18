# Architecture

## 1. Overview

The agent answers questions about **long-term weather exposure** of the company's hubs: which hubs
are most exposed, why, and how they compare. It is built for prioritizing resilience investment,
so it deliberately does **not** cover short-term operational risk (this week's forecast, active
alerts).

The core design decision: **numbers come from code, words come from the LLM.** Every score and
count is computed deterministically and stored in MongoDB; the LLM only chooses which data tools to
call, and explains the results in a fixed JSON structure.

## 2. Assumptions, uncertainty and scope

- **One hub per state, located at the state capital.** Questions about other cities (e.g. Miami,
  Houston) are answered with that state's hub, and the agent says so.
- **Regions** are the US Census regions.
- **Thresholds and weights are estimates** of operational limits, not calibrated against the
  company's actual disruption history.
- **Hurricanes, floods, storms are not measured directly - but infered from the raw weather data** Storm days (wind + rain) are a proxy;
  storm surge and river flooding are not covered.
- **Data source:** Open-Meteo's archive lags by about 5 days.
- **The daily update uses the forecast API's past days**, a different model from the archive used
  for the seed; small inconsistencies between the two are possible.
- **A 3-year window** can over- or under-represent rare events.
- **Score changes:** every change is recorded, however small. "Seen" in the UI is per browser,
  not per user.
- **Out of scope:** short-term forecasts and active alerts, authentication, multi-user access.

## 3. Main Components

| Component | Role |
|---|---|
| **Web** (React + Vite, served by nginx) | Chat UI with conversation history and an updates bell widget. nginx proxies `/api` to the backend. |
| **API** (FastAPI) | Exposes the agent and read endpoints. Loads/saves the conversation for each chat turn. |
| **Agent** (pydantic-ai + Claude Haiku) | Answers questions by calling four tools. Its output must validate against `AgentAnswer`. |
| **Scoring** (`score.py`) | Pure functions: which days are extreme, the score, counts per component and weather type. |
| **Daily update** (`daily_update.py`) | Fetches new days, stores extreme days, recalculates every hub's score, records each change. |
| **MongoDB** | contain the docs with the assets as well as job runs, conversations. |
| **Logfire** (optional) | Traces of every request, agent run, model call, tool call and Open-Meteo call. |

### Request flow (one chat turn)
1. The UI sends `POST /api/chat {session_id, message}`.
2. The API loads the conversation history from MongoDB and runs the agent with it, so follow-up
   questions ("and why?") resolve against earlier turns.
3. The agent calls tools (at most 10 model requests per turn), e.g. `get_top_k_hubs(region="Midwest")`.
4. The final output is validated against the `AgentAnswer` schema; if it does not validate,
   pydantic-ai sends the error back to the model to retry.
5. The new messages are appended to the conversation; the UI renders `answer` (markdown) and
   `assumptions` (as assumptions under the answer).

### The LLM ↔ code boundary
- **Input to the LLM:** tool results only — typed Pydantic models / dicts built from the DB.
  The system prompt forbids using general climate knowledge as evidence.
- **Output from the LLM:** a JSON object enforced by schema:
  ```json
  { "answer": "markdown for the analyst",
    "hubs": [{"hub_id": "ND", "score": 87.5}],
    "assumptions": ["caveats that apply to this answer"] }
  ```
- **Tools:**

| Tool | Data | Used for |
|---|---|---|
| `get_top_k_hubs(k, region? weather?)` | MongoDB | rankings |
| `get_region_summary(region)` | MongoDB | region totals and averages |
| `get_hub_details(hub_id)` | MongoDB | explaining a score (counts per component, recent extreme days) |
| `get_hub_weather_history(hub_id, year)` | Open-Meteo Archive | ordinary weather in a given year ("% of days with snow") |

Invalid arguments (unknown hub, year out of range) raise `ModelRetry` with the valid values, so
the model corrects itself instead of guessing.

## 4. Risk scoring logic

A day is **extreme** in a component when it crosses a fixed operational threshold (See Assumptions, uncertainty and scope):

| Component | Threshold | Weight |
|---|---|---|
| low temperature | daily min ≤ −5 °C | 1.5 |
| high temperature | daily max ≥ 38 °C | 1 |
| wind speed | daily max ≥ 65 km/h | 1.5 |
| wind gusts | daily max ≥ 80 km/h | 2 |
| rain | daily precipitation ≥ 50 mm | 1.5 |
| snow | daily snowfall ≥ 5 cm | 2 |

**Score = Σ weights of all extreme components over the last 3 years ÷ 3**, i.e. weighted extreme
days per year. A day can count in more than one component.

**Normalized score (1–100):** the raw score rescaled across all hubs (min-max), so the least
exposed hub is 1 and the most exposed is 100. It is computed when the agent reads scores, not
stored: it is relative, so one hub's change would shift every other hub's normalized score.
The raw score stays the stored value and drives the alerts.

**Why these weights.** Each weight reflects how likely that kind of day is to stop trucks, since
road transport is what a hub depends on:
- **Snow and wind gusts (2)** most often shut operations down: snow closes roads, and strong
  gusts lead to travel restrictions for high-profile vehicles because they can tip empty trailers.
- **Low temperature, sustained wind and heavy rain (1.5)** cause ice on roads, restrictions for
  trucks and local flooding. They slow deliveries more often than they stop them.
- **High temperature (1)** strains workers and equipment but rarely closes roads.

The weights are a judgment call, not fitted to data (see section 2).

**Inferred weather types** (reported to explain answers, not part of the score): a combination of
measurements on the same day, e.g. *storm* = gusts ≥ 60 km/h and precipitation ≥ 20 mm. They are
the system's proxy for hurricane/flood questions.

## 5. Repository structure

```
├── docker-compose.yml          MongoDB + agent + web
├── .env.example
├── docs/
│   ├── ARCHITECTURE.md         this document
│   └── ai-session/             exported sessions with the AI coding agent
├── evals/weather_agent/
│   ├── cases.json              25 eval cases (questions + how to grade them)
│   ├── cases.md                the same cases, human-readable
│   └── run_eval.py             pydantic-evals dataset: runs the real agent, grades against ground truth from the DB
└── src/
    ├── backend/
    │   ├── main.py             FastAPI app; starts the daily update task
    │   ├── api.py              HTTP endpoints
    │   ├── agent.py            system prompt, agent, tools
    │   ├── score.py            thresholds, weights, score calculation
    │   ├── daily_update.py     scheduled score refresh
    │   ├── seed.py             one-time hub creation and 3-year backfill
    │   ├── db.py               all MongoDB access
    │   ├── weather_client.py   Open-Meteo client
    │   └── models.py           Pydantic models (domain, LLM output, API)
    └── web/src/
        ├── App.tsx             state: conversations, messages
        ├── api.ts              typed API client
        └── components/         Chat, Sidebar, AlertsBell
```

## 6. Data storage

I chose **MongoDB** because:
- **The data is small and document-shaped.** Raw weather stays in the Open-Meteo API; only
  derived data is stored (hubs, extreme days, score changes), and every query is a lookup or sort
  by hub. No joins or transactions across collections are needed.
- **The data is already JSON.** Pydantic models are dumped to documents and validated back
  without an ORM or schema migrations. Lists like an extreme day's `components` are plain arrays.
- **Conversation history is nested JSON of varying shape** (pydantic-ai's message list), so it is
  stored as-is in one document per conversation.

A relational database would also work for the scoring data. The trade-off is that the schema is
not enforced by the database: the Pydantic models are the schema.

| Collection | Contents | Index |
|---|---|---|
| `hubs` | one doc per hub; current score embedded | `id` (unique), `region`, `score.score` |
| `extreme_days` | one doc per (hub, day) that crossed a threshold or matched a weather type | `(hub_id, day)` unique |
| `score_changes` | append-only history: old score, new score, reasons | `(hub_id, changed_at)` |
| `job_runs` | every daily update run, success or failure, with its date range | `(status, run_at)` |
| `conversations` | `{_id: session_id, title, updated_at, messages: [...]}` | — |

**Not stored:** raw daily weather. Only extreme days are kept, because the score only needs them;
ordinary-weather questions are answered live from the archive API.

## 7. Daily update and alerts

- A background task in the API process runs once a day (`UPDATE_AT_UTC`, default 06:00).
- It fetches every day since the last **successful** run (so a failed run is covered by the next
  one), stores new extreme days, and recalculates every hub's score.
- **Any** change in a hub's score is saved and recorded in `score_changes` with its reasons.
- `GET /alerts` returns only changes of at least `ALERT_MIN_DELTA` score points (default 1, up or
  down); smaller changes and a hub's first score stay in the history only.
- The bell in the UI is colored when there are alerts this browser has not seen yet; opening it
  shows only those unread alerts and marks them as read.

## 8. Observability

The backend sends traces to [Logfire](https://logfire.pydantic.dev) (set up in `main.py`):

- **FastAPI** - one span per request (`POST /chat`, `GET /alerts`, ...) with status and duration.
- **pydantic-ai** - inside `/chat`: the agent run, each model call (prompt, response, input/output
  tokens, latency) and each tool call with its arguments and result.
- **httpx** - the Open-Meteo calls made by the tools and the daily update.

So one chat turn is a single trace that shows how many model requests it took, where the time
went (model vs. tools vs. weather API) and what it cost in tokens.
Sending is enabled only when `LOGFIRE_TOKEN` is set; without it the app runs the same and nothing
is sent.

## 9. Evaluation

25 cases in five categories: ranking, numbers, ordinary-weather history, scope (no such hub,
short-term forecast, year without data), and tool use / multi-turn. One question is in Hebrew.

- **Ground truth is computed at run time** from the DB and the archive API, so the eval stays
  valid after a reseed.
- Built on **pydantic-evals**: each case is a `Case`, and every run is graded by three evaluators:
  - `Correct` - factual cases are graded by code (the correct hubs, in order; the exact numbers);
    scope cases use the built-in `LLMJudge` (Claude Sonnet) against explicit claims.
  - `ToolsOk` - `update_scores` is called only where the case expects it.
  - `WithinLimit` - the answer finished within the 10-request limit.
- Each case runs N times. Requests, tokens and cost per case are taken from the agent's spans;
  the report goes to the terminal and, with `LOGFIRE_TOKEN`, to Logfire.

**Latest result:** 98.7% of assertions passed (`Correct`, `ToolsOk`, `WithinLimit`), 25 cases × 2 reps,
Claude Haiku. On average 2.1 model requests, 1.4 tool calls and 5.3 s per case.


## 10. What I would do with more time

- Calibrate thresholds and weights against real hub closure data.