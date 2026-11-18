# Weather Risk Intelligence Agent

An AI agent that helps logistics analysts decide which distribution hubs are most exposed to
weather disruption in order to be prioritized for investment.
Risk scores are computed deterministically from 3 years of historical weather

Design, scoring logic, data storage and assumptions: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run with Docker (recommended)

Requires Docker and an Anthropic API key. Optional: a [Logfire](https://logfire.pydantic.dev)
write token (`LOGFIRE_TOKEN`) to send traces of the agent; without it nothing is sent.

```bash
cp .env.example .env              # then set ANTHROPIC_API_KEY (and optionally LOGFIRE_TOKEN for tracing)
docker compose up -d --build      # MongoDB + API (:8000) + web UI (:3000)

# one-time: create the 50 hubs and load 3 years of history (takes a few minutes)
docker compose exec agent uv run --no-sync python seed.py
```

Open http://localhost:3000.

## Run locally (development)

Requires Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+, and MongoDB on
`localhost:27017` (e.g. `docker compose up -d db`). The backend reads `ANTHROPIC_API_KEY` and the
optional `LOGFIRE_TOKEN` from the root `.env`.

```bash
# backend
cd src/backend
uv sync
uv run python seed.py             # one-time
uv run uvicorn main:app --reload  # http://localhost:8000

# web (second terminal)
cd src/web
npm install
npm run dev                       # http://localhost:5173, proxies /api to :8000
```

## Run the evaluation

25 cases (ranking, numbers, history, scope, multi-turn), graded against ground truth computed
from the DB at run time. Needs MongoDB with seeded data and `ANTHROPIC_API_KEY`.

```bash
# with Docker (uses the db container; --build picks up code changes)
docker compose run --rm --build eval --name baseline --reps 2

# or locally
cd src/backend
uv run python ../../evals/weather_agent/run_eval.py --name baseline --reps 2
```

Built on [pydantic-evals](https://ai.pydantic.dev/evals/). The report (pass/fail with the reason
per case, requests, tokens, cost and duration) is printed to the terminal; with `LOGFIRE_TOKEN`
set it is also sent to Logfire, where experiments can be compared. `--only r01,s03` runs some
cases. The case list is in [evals/weather_agent/cases.md](evals/weather_agent/cases.md).
