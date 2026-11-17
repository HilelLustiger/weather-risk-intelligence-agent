# Weather Risk Intelligence Agent

An AI agent that helps logistics analysts decide which distribution hubs are most exposed to
weather disruption in order to be prioritized for investment.
Risk scores are computed deterministically from 3 years of historical weather

Design, scoring logic, data storage and assumptions: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run with Docker (recommended)

Requires Docker and an Anthropic API key.

```bash
cp .env.example .env              # then set ANTHROPIC_API_KEY
docker compose up -d --build      # MongoDB + API (:8000) + web UI (:3000)

# one-time: create the 50 hubs and load 3 years of history (takes a few minutes)
docker compose exec agent uv run --no-sync python seed.py
```

Open http://localhost:3000.

## Run locally (development)

Requires Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+, and MongoDB on
`localhost:27017` (e.g. `docker compose up -d db`).

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
cd src/backend
uv run python ../../evals/weather_agent/run_eval.py --variant baseline --reps 2 --approve-harness
```

`--approve-harness` is required on the first run and after editing the cases or the runner.
Results are written to `.claude/hillclimb/weather_agent/<variant>/` (one row per case and rep,
plus full traces). The case list is in [evals/weather_agent/cases.md](evals/weather_agent/cases.md).
