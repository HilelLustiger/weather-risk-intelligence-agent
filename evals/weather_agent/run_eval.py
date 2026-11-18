"""Eval for the weather-risk agent, built on pydantic-evals.

Every case in cases.json runs through the real agent (agent.run - the same call /chat makes)
against the real DB and is graded by:
    Correct      - code checks against ground truth computed from the DB / archive at run time
                   (scope cases use LLMJudge instead, reported under the same name)
    ToolsOk      - update_scores was called only where the case expects it
    WithinLimit  - the answer finished within the request limit
Metrics per case: tool_calls; requests, tokens and cost are collected by pydantic-evals from the
agent's spans (logfire.instrument_pydantic_ai), plus duration.
The report is printed to the terminal, and sent to Logfire when LOGFIRE_TOKEN is set.

Side effects are blocked: the DB is wrapped read-only and the forecast client used by
update_scores is a stub, so a call to update_scores is recorded but writes nothing.

Usage (from src/backend, with Mongo running):
    uv run python ../../evals/weather_agent/run_eval.py --reps 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src" / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
# The Anthropic SDK emits its own span inside pydantic-ai's model-call span; pydantic-evals would count
# requests and tokens from both. Read when the client is created, so it must come before `import agent`.
os.environ.setdefault("ANTHROPIC_OPEN_TELEMETRY", "false")

import logfire  # noqa: E402
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded  # noqa: E402
from pydantic_ai.messages import ModelResponse, ToolCallPart  # noqa: E402
from pydantic_ai.usage import UsageLimits  # noqa: E402
from pydantic_evals import Case, Dataset, increment_eval_metric, set_eval_attribute  # noqa: E402
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext, LLMJudge  # noqa: E402

import agent as app  # noqa: E402
from db import Database  # noqa: E402
from score import component_counts, daily_summary, weather_type_counts  # noqa: E402
from weather_client import OPEN_METEO_ARCHIVE_URL, WeatherClient  # noqa: E402

CASES_PATH = HERE / "cases.json"
REQUEST_LIMIT = 10  # same as api.py
JUDGE_MODEL = "anthropic:claude-sonnet-5-5"

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
# 2-letter codes that are also common English words - matched by full name/capital only
AMBIGUOUS_CODES = {"IN", "OR", "ME", "OK", "HI", "OH", "AL", "LA", "PA", "MA", "CO", "DE", "ID", "MO"}
REGIONS = ["Northeast", "Midwest", "South", "West"]


# --------------------------------------------------------------------------- sandbox

class ReadOnlyDatabase(Database):
    """Real reads, no writes. update_scores still runs its real code path."""

    async def add_extreme_day(self, day):  # noqa: D102
        return None

    async def change_score(self, hub_id, new_score, reasons):  # noqa: D102
        return None

    async def save_conversation(self, conversation_id, messages, title=""):  # noqa: D102
        return None


class StubForecast:
    """Replaces the forecast client: no Open-Meteo calls from update_scores."""

    async def get_weather(self, hub, start, end):
        return []

    async def close(self):
        return None


# --------------------------------------------------------------------------- ground truth

class Truth:
    """Computes and caches the correct answers from the DB / archive."""

    def __init__(self, db: Database, archive: WeatherClient):
        self.db, self.archive = db, archive
        self.hubs: dict = {}
        self._history: dict = {}

    async def load(self):
        self.hubs = {h.id: h for h in await self.db.get_hubs()}

    def ranked(self, region=None):
        hubs = [h for h in self.hubs.values() if h.score and (region is None or h.region == region)]
        return sorted(hubs, key=lambda h: h.score.score, reverse=True)

    async def counts(self, hub_id):
        days = await self.db.get_extreme_days(hub_id)
        return component_counts(days), weather_type_counts(days), days

    async def region_totals(self, region, weather_type):
        total = 0
        for h in self.hubs.values():
            if h.region == region:
                total += (await self.counts(h.id))[1][weather_type]
        return total

    async def history(self, hub_id, year):
        key = (hub_id, year)
        if key not in self._history:
            hub = self.hubs[hub_id]
            end = min(date(year, 12, 31), date.fromordinal(date.today().toordinal() - 1))  # same range as the tool
            self._history[key] = daily_summary(await self.archive.get_weather(hub, date(year, 1, 1), end))
        return self._history[key]


# --------------------------------------------------------------------------- matching helpers

def hub_pattern(hub) -> re.Pattern:
    names = [re.escape(STATE_NAMES[hub.id]), re.escape(hub.name)]
    if hub.id not in AMBIGUOUS_CODES:
        names.append(rf"(?<![A-Za-z]){hub.id}(?![A-Za-z])")
    return re.compile("|".join(names))


def first_pos(text, hub) -> int:
    m = hub_pattern(hub).search(text)
    return m.start() if m else -1


def norm(text) -> str:
    """Lowercase and treat '-' / '_' as spaces, so 'low-temperature' matches 'low temperature'."""
    return re.sub(r"[-_\s]+", " ", text.lower())


def numbers_in(text) -> list[float]:
    return [float(n.replace(",", "")) for n in re.findall(r"-?\d[\d,]*\.?\d*", text)]


def has_number(text, value, tol=0.0) -> bool:
    return any(abs(n - value) <= tol + 1e-9 for n in numbers_in(text))


def has_date(text, d: date) -> bool:
    forms = [d.isoformat(), d.strftime("%B %-d"), d.strftime("%b %-d"), d.strftime("%-d %B"),
             d.strftime("%m/%d/%Y"), d.strftime("%-m/%-d/%Y")]
    return any(f.lower() in text.lower() for f in forms)


# --------------------------------------------------------------------------- code grader

async def grade(c: dict, answer: str, truth: Truth) -> tuple[bool, str]:
    """Grades one answer against a "check" from cases.json. Returns (correct, explanation)."""
    t = c["type"]

    if t in ("top_k_all", "top_k_ordered", "top_1"):
        k = c.get("k", 1)
        expected = truth.ranked(c.get("region"))[:k]
        pos = [first_pos(answer, h) for h in expected]
        ids = [h.id for h in expected]
        if -1 in pos:
            return False, f"missing hubs: {[i for i, p in zip(ids, pos) if p == -1]} (expected {ids})"
        if t == "top_k_ordered" and pos != sorted(pos):
            return False, f"wrong order (expected {ids})"
        if t == "top_1":
            others = [first_pos(answer, h) for h in truth.hubs.values() if h.id != ids[0]]
            if any(-1 < p < pos[0] for p in others):
                return False, f"another hub is named before the top hub {ids[0]}"
        return True, f"expected {ids}"

    if t == "top_region_avg":
        avgs = {}
        for r in REGIONS:
            s = [h.score.score for h in truth.ranked(r)]
            avgs[r] = sum(s) / len(s)
        best = max(avgs, key=avgs.get)
        first = min((answer.find(r), r) for r in REGIONS if r in answer) if any(r in answer for r in REGIONS) else (-1, None)
        return first[1] == best, f"expected {best} (avgs {', '.join(f'{r}={v:.1f}' for r, v in avgs.items())})"

    if t == "hub_score":
        missing = [h for h in c["hubs"] if not has_number(answer, round(truth.hubs[h].score.score, 1), tol=0.1)]
        exp = {h: round(truth.hubs[h].score.score, 2) for h in c["hubs"]}
        return not missing, f"expected scores {exp}" + (f"; missing {missing}" if missing else "")

    if t == "dominant_component":
        hub_id = c["hub"]
        if hub_id.startswith("top_1:"):
            hub_id = truth.ranked(hub_id.split(":")[1])[0].id
        comps, _, _ = await truth.counts(hub_id)
        top, n = max(comps.items(), key=lambda kv: kv[1])
        ok = norm(top) in norm(answer) and has_number(answer, n)
        return ok, f"{hub_id}: expected '{top}' with {n} days"

    if t == "component_count":
        comps, _, _ = await truth.counts(c["hub"])
        n = comps[c["component"]]
        ok = has_number(answer, n) and c["must_mention"].replace(" ", "") in answer.replace(" ", "")
        return ok, f"expected {n} {c['component']} days and the threshold '{c['must_mention']}'"

    if t == "region_weather_type":
        totals = {r: await truth.region_totals(r, c["weather_type"]) for r in c["regions"]}
        missing = [r for r, v in totals.items() if not has_number(answer, v)]
        return not missing, f"expected {c['weather_type']} totals {totals}" + (f"; missing {missing}" if missing else "")

    if t == "history":
        year = date.today().year - 1 if c["year"] == "last" else c["year"]
        n = (await truth.history(c["hub"], year))[c["field"]]
        return has_number(answer, n), f"expected {n} ({c['field']}, {c['hub']} {year})"

    if t == "latest_dates":
        _, _, days = await truth.counts(c["hub"])
        latest = [d.day for d in days[::-1][:5]]
        hit = [d for d in latest if has_date(answer, d)]
        return bool(hit) and has_date(answer, latest[0]), f"expected the most recent {latest[0]} (latest 5: {[str(d) for d in latest]})"

    raise ValueError(f"unknown check type {t}")


# --------------------------------------------------------------------------- evaluators

@dataclass
class Correct(Evaluator):
    truth: Truth

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        if ctx.attributes.get("hit_limit"):
            return EvaluationReason(False, f"stopped at the {REQUEST_LIMIT}-request limit")
        ok, why = await grade(ctx.metadata["check"], ctx.output, self.truth)
        return EvaluationReason(ok, why)


@dataclass
class ToolsOk(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        called = "update_scores" in ctx.attributes.get("tools", [])
        expected = ctx.metadata.get("expect_update_scores", False)
        return EvaluationReason(called == expected, f"update_scores called={called}, expected={expected}")


@dataclass
class WithinLimit(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> bool:
        return not ctx.attributes.get("hit_limit", False)


def judge(claims: list[str]) -> LLMJudge:
    rubric = ("The answer clearly satisfies ALL of these claims (do not reward length):\n"
              + "\n".join(f"{i + 1}. {cl}" for i, cl in enumerate(claims)))
    return LLMJudge(rubric=rubric, model=JUDGE_MODEL, include_input=True,
                    assertion={"evaluation_name": "Correct", "include_reason": True})


# --------------------------------------------------------------------------- task

async def run_turn(turn: str, history: list, deps):
    """agent.run with backoff on rate-limit / overload errors (429 / 529)."""
    for attempt in range(4):
        try:
            return await app.agent.run(turn, message_history=history, deps=deps,
                                       usage_limits=UsageLimits(request_limit=REQUEST_LIMIT))
        except ModelHTTPError as e:
            if e.status_code not in (429, 529) or attempt == 3:
                raise
            await asyncio.sleep(min(60, 2 ** attempt * 5) + random.uniform(0, 3))


def make_task(deps):
    async def ask_agent(turns: list[str]) -> str:
        """Runs the conversation turn by turn (each turn sees the earlier ones) and returns the last answer."""
        history, answer = [], ""
        for turn in turns:
            try:
                result = await run_turn(turn, history, deps)
            except UsageLimitExceeded:
                set_eval_attribute("hit_limit", True)
                break
            history, answer = result.all_messages(), result.output.answer
        tools = [p.tool_name for m in history if isinstance(m, ModelResponse)
                 for p in m.parts if isinstance(p, ToolCallPart) and p.tool_name != "final_result"]
        set_eval_attribute("tools", tools)
        increment_eval_metric("tool_calls", len(tools))
        return answer
    return ask_agent


# --------------------------------------------------------------------------- main

def build_dataset(cases: list[dict], truth: Truth) -> Dataset:
    return Dataset(
        name="weather_agent",
        cases=[Case(name=c["id"], inputs=c["turns"], metadata=c,
                    evaluators=(judge(c["check"]["claims"]) if c["check"]["type"] == "judge" else Correct(truth),))
               for c in cases],
        evaluators=[ToolsOk(), WithinLimit()],
    )


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="baseline", help="experiment name (shown in the report / Logfire)")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--timeout-s", type=float, default=180)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--only", help="comma-separated case ids")
    args = ap.parse_args()

    logfire.configure(service_name="weather-risk-evals", send_to_logfire="if-token-present", console=False)
    logfire.instrument_pydantic_ai()

    cases = json.loads(CASES_PATH.read_text())
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]

    db = ReadOnlyDatabase()
    archive = WeatherClient(base_url=OPEN_METEO_ARCHIVE_URL, path="/archive", timeout=30)
    deps = app.Deps(db=db, weather=StubForecast(), archive=archive)
    truth = Truth(db, archive)
    await truth.load()

    try:
        report = await build_dataset(cases, truth).evaluate(
            make_task(deps), name=args.name, repeat=args.reps,
            max_concurrency=args.concurrency, task_timeout=args.timeout_s,
            metadata={"model": app.MODEL_NAME})
        report.print(include_reasons=True)
    finally:
        await archive.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
