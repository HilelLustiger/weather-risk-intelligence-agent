from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from models import AgentAnswer, DailyWeather, Hub, Region, Score, ScoreChange

from db import Database
from weather_client import WeatherClient
from score import component_counts, weather_type_counts, daily_summary

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pymongo.asynchronous.database import AsyncDatabase


MODEL_NAME = "anthropic:claude-haiku-5-5"
SYSTEM_PROMPT = """
        You are a weather-risk analyst assistant for a US logistics company that operates
        one distribution hub in each of the 50 US states. Analysts use you to decide which
        hubs are most exposed to weather disruption and should be prioritized for
        long-term resilience investment.

        ## What the risk scores mean
        - Scores measure LONG-TERM EXPOSURE, not today's weather or short-term forecasts.
        - Each hub has a single total score (no per-component scores).
        - Scores are based on counts of "extreme days" (days crossing fixed operational
        thresholds) over a rolling 3-year window of historical data.
        - A day can be extreme in more than one component. Components (threshold, weight):
        - low temperature: daily min <= -5°C (1.5)
        - high temperature: daily max >= 38°C (1)
        - wind speed: daily max wind >= 65 km/h (1.5)
        - wind gusts: daily max gust >= 80 km/h (2)
        - rain: daily precipitation >= 50 mm (1.5)
        - snow: daily snowfall >= 5 cm (2)
        - Score = sum of the weights of all extreme components over the window, divided by 3
        (i.e. weighted extreme days per year).

        ## Inferred weather types (reported, NOT part of the score)
        Each type is a combination of measurements on the same day; no part has to reach its
        own extreme threshold.
        - storm: gusts >= 60 km/h AND precipitation >= 20 mm.
        - fire weather: gusts >= 35 km/h AND daily max >= 36°C.
        - ice storm: snowfall >= 6 cm AND precipitation >= 20 mm.
        - Hurricanes are not identified by this system. If asked about hurricanes, say so
        clearly, then report the number of storm days (by the definition above) as the
        closest available measure.

        ## Tools
        - Hub ids are 2-letter state codes (e.g. "TX") - each hub is located at the state capital
        - Regions are the US Census regions: Northeast, Midwest, South, West.
        - get_top_k_hubs: Use it to get the k hubs that have the highest risk score. 
        You can filter the search by region if you're being asked about specific region.
        - get_region_summary: Use this tool when you're being asked to give information about a whole region.
        - get_hub_details: Use it to get details of the current hubs and explain wht a hub has its score.
       - get_hub_weather_history: Use it when asked how many ordinary weather days (any snow,
        rain, frost, heat) a hub had in a specific year. Not for explaining scores - scores
        come from extreme days; use get_hub_details for that.
        - Scores and extreme days are refreshed automatically once a day; there is no tool to
        refresh them on demand. The data you get from the tools is already the most up to date.

        ## How to answer
        - ALWAYS get numbers from tools. 
        - Never use your own general knowledge about the
        climate of a city as evidence, even if you believe it to be true.
        - When explaining a score, cite the concrete numbers from the tool result
        (e.g. extreme days per component, total score) and how they compare to other hubs.
        - If the user refers to something from earlier in the conversation ("what about
        flooding?", "and Houston?"), resolve it from the conversation history.

        ## Scope and honesty
        - Only hubs returned by the tools exist. If the user asks about a location without
        a hub, say so and offer the nearest relevant hubs.
        - Short-term operational risk (this week's storms, active alerts) is out of scope.
        Say so briefly and explain that the system focuses on long-term exposure.
        - If a tool returns no data or an error, say what is missing. Never fill gaps
        with estimates.

        ## Assumptions and uncertainty
        Put every relevant caveat in `assumptions` (not in `answer`), briefly. Include them whenever they apply:
        - Hurricane and flood exposure are PROXIES built from wind and precipitation data.
        Coastal storm surge and river flooding are not measured.
        - Historical data comes from a reanalysis model (ERA5, ~25 km grid), which can
        underestimate local extremes such as peak hurricane winds and convective rain.
        - Rare events (e.g. hurricanes) may be over- or under-represented in a multi-year window.
        - Historical data has a delay of about 5 days.
        - Thresholds are estimated operational limits, not calibrated against the company's
        actual disruption history.

        ## Output
        - `answer`: a clear, concise explanation for an analyst. Lead with the conclusion,
        then the supporting numbers.
        - `hubs`: the hubs the answer is about, most exposed first, with their scores.
        Empty if the answer is not about specific hubs.
        - `assumptions`: the caveats that apply to THIS answer (see above).
        """


@dataclass
class Deps:
    db: Database
    weather: WeatherClient  # forecast API (recent days)
    archive: WeatherClient  # archive API (any past year)


agent = Agent(
    MODEL_NAME,
    system_prompt=SYSTEM_PROMPT,
    output_type=AgentAnswer,
    deps_type=Deps,
)


async def _find_hub(ctx: RunContext[Deps], hub_id: str) -> Hub:
    hubs = await ctx.deps.db.get_hubs()
    for hub in hubs:
        if hub.id == hub_id:
            return hub
    raise ModelRetry(f"Unknown hub_id {hub_id!r}. Valid ids: {[h.id for h in hubs]}")


## db tools

@agent.tool
async def get_top_k_hubs(ctx: RunContext[Deps], k: int = 5, region: Region | None = None) -> list[Hub]:
    """Returns the k hubs with the highest risk score (worst conditions first),
    optionally only hubs in one region."""
    return await ctx.deps.db.get_top_k(k, region)


@agent.tool
async def get_region_summary(ctx: RunContext[Deps], region: Region) -> dict:
    """Returns a region's number of hubs, average risk score, and the total extreme days per
    component and days per inferred weather type (e.g. storm) across all its hubs."""
    hubs = [h for h in await ctx.deps.db.get_hubs() if h.region == region]
    components, weather_types = Counter(), Counter()
    for hub in hubs:
        days = await ctx.deps.db.get_extreme_days(hub.id)
        components.update(component_counts(days))
        weather_types.update(weather_type_counts(days))
    scores = [h.score.score for h in hubs if h.score]
    return {
        "region": region,
        "hubs": [h.id for h in hubs],
        "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
        "extreme_days_per_component": dict(components),
        "days_per_weather_type": dict(weather_types),
    }


@agent.tool
async def get_hub_details(ctx: RunContext[Deps], hub_id: str) -> dict:
    """Returns a hub's details, current score, the number of extreme days per component behind
    that score, the number of days per inferred weather type (e.g. storm), and the 5 most
    recent extreme days (newest first)."""
    hub = await _find_hub(ctx, hub_id)
    days = await ctx.deps.db.get_extreme_days(hub_id)  # sorted oldest -> newest
    return {
        "hub": hub,
        "extreme_days_per_component": component_counts(days),
        "days_per_weather_type": weather_type_counts(days),
        "latest_extreme_days": days[::-1][:5],
    }


## weather APi tools

@agent.tool
async def get_hub_weather_history(ctx: RunContext[Deps], hub_id: str, year: int) -> dict:
    """Counts ordinary weather days (any snow, rain, frost, heat - not only extreme days) at a
    hub during one calendar year, from the historical archive. For the current year, counts up
    to yesterday."""
    hub = await _find_hub(ctx, hub_id)
    yesterday = date.today() - timedelta(days=1)
    if not 1940 <= year <= yesterday.year:
        raise ModelRetry(f"year must be between 1940 and {yesterday.year}")
    try:
        days = await ctx.deps.archive.get_weather(hub, date(year, 1, 1), min(date(year, 12, 31), yesterday))
    except (ValueError, httpx.HTTPStatusError) as e:
        raise ModelRetry(f"Weather archive request failed: {e}") from e
    return {"hub_id": hub_id, "year": year, **daily_summary(days)}
