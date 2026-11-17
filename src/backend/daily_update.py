"""Daily score refresh. Runs as a background task started in main.py's lifespan.
Every run is recorded in the job_runs collection (success or failure)."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone

from db import Database
from models import JobRun, ScoreChange
from score import calculate, is_extreme
from weather_client import WeatherClient

UPDATE_AT_UTC = os.getenv("UPDATE_AT_UTC", "06:00")  # HH:MM, once a day
FIRST_RUN_DAYS = 2  # when there is no successful run yet (right after the seed)
MAX_DAYS_BACK = 92  # the forecast API's limit for past days

log = logging.getLogger("uvicorn.error")  # shows up in the API's logs


def date_range(last_success: JobRun | None, today: date) -> tuple[date, date]:
    """From the day after the last successful run up to yesterday, at most MAX_DAYS_BACK days.
    Days covered by failed runs are therefore picked up by the next successful one."""
    end = today - timedelta(days=1)
    start = last_success.end_date + timedelta(days=1) if last_success else end - timedelta(days=FIRST_RUN_DAYS - 1)
    return max(start, end - timedelta(days=MAX_DAYS_BACK - 1)), end


async def update_scores(db: Database, weather: WeatherClient, start: date, end: date) -> list[ScoreChange]:
    """Fetches start..end for every hub, saves extreme days, recalculates scores and saves
    (and returns) a ScoreChange for every hub whose score changed at all."""
    changes = []
    for hub in await db.get_hubs():
        extremes = [e for day in await weather.get_weather(hub, start, end) if (e := is_extreme(day))]
        for e in extremes:
            await db.add_extreme_day(e)

        new_score = calculate(hub.id, await db.get_extreme_days(hub.id))
        if hub.score is None or new_score.score != hub.score.score:
            reasons = "; ".join(f"{e.day}: {', '.join(e.components)}" for e in extremes) or "extreme days left the window"
            changes.append(await db.change_score(hub.id, new_score, reasons))
    return changes


async def run_once(db: Database, weather: WeatherClient) -> JobRun | None:
    """One daily update, recorded in job_runs. Returns None if there is nothing new to fetch."""
    start, end = date_range(await db.get_last_successful_run(), date.today())
    if start > end:
        log.info("daily update: already up to date (last success covered %s)", end)
        return None

    run = JobRun(id=uuid.uuid4().hex, run_at=datetime.now(timezone.utc), status="success", start_date=start, end_date=end)
    try:
        run.changed_hubs = [c.hub_id for c in await update_scores(db, weather, start, end)]
        log.info("daily update %s: %s..%s, %d score(s) changed %s", run.id, start, end, len(run.changed_hubs), run.changed_hubs)
    except Exception as e:
        run.status, run.error = "failure", f"{type(e).__name__}: {e}"[:500]
        log.exception("daily update %s failed (%s..%s); the next run will cover these days", run.id, start, end)
    await db.add_job_run(run)
    return run


def seconds_until(hhmm: str, now: datetime | None = None) -> float:
    """Seconds from `now` until the next HH:MM (UTC)."""
    now = now or datetime.now(timezone.utc)
    hour, minute = map(int, hhmm.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_daily(db: Database, weather: WeatherClient) -> None:
    """Sleeps until UPDATE_AT_UTC, runs one update, repeats. Never stops the API."""
    while True:
        wait = seconds_until(UPDATE_AT_UTC)
        log.info("daily update: next run at %s UTC (in %.1f h)", UPDATE_AT_UTC, wait / 3600)
        await asyncio.sleep(wait)
        try:
            await run_once(db, weather)
        except Exception:  # e.g. the DB itself is unreachable - can't even record the run
            log.exception("daily update: could not run or record the job")
