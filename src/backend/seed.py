"""One-time setup: creates the hubs and loads WINDOW_YEARS of extreme-day history from the
Open-Meteo archive. Safe to re-run. Usage (from src/backend): uv run python seed.py"""
from dotenv import load_dotenv

load_dotenv()  # must run before importing db (reads env vars at import time)

import asyncio
from datetime import date, timedelta

import httpx

from db import Database
from models import Hub, Region
from score import WINDOW_YEARS, calculate, is_extreme
from weather_client import OPEN_METEO_ARCHIVE_URL, WeatherClient

# (state, capital, region, lat, lon) - hub per state at its capital, US Census regions
HUBS: list[tuple[str, str, Region, float, float]] = [
    ("AL", "Montgomery",     "South",     32.3668,  -86.3000),
    ("AK", "Juneau",         "West",      58.3019, -134.4197),
    ("AZ", "Phoenix",        "West",      33.4484, -112.0740),
    ("AR", "Little Rock",    "South",     34.7465,  -92.2896),
    ("CA", "Sacramento",     "West",      38.5816, -121.4944),
    ("CO", "Denver",         "West",      39.7392, -104.9903),
    ("CT", "Hartford",       "Northeast", 41.7658,  -72.6734),
    ("DE", "Dover",          "South",     39.1582,  -75.5244),
    ("FL", "Tallahassee",    "South",     30.4383,  -84.2807),
    ("GA", "Atlanta",        "South",     33.7490,  -84.3880),
    ("HI", "Honolulu",       "West",      21.3069, -157.8583),
    ("ID", "Boise",          "West",      43.6150, -116.2023),
    ("IL", "Springfield",    "Midwest",   39.7817,  -89.6501),
    ("IN", "Indianapolis",   "Midwest",   39.7684,  -86.1581),
    ("IA", "Des Moines",     "Midwest",   41.5868,  -93.6250),
    ("KS", "Topeka",         "Midwest",   39.0473,  -95.6752),
    ("KY", "Frankfort",      "South",     38.2009,  -84.8733),
    ("LA", "Baton Rouge",    "South",     30.4515,  -91.1871),
    ("ME", "Augusta",        "Northeast", 44.3106,  -69.7795),
    ("MD", "Annapolis",      "South",     38.9784,  -76.4922),
    ("MA", "Boston",         "Northeast", 42.3601,  -71.0589),
    ("MI", "Lansing",        "Midwest",   42.7325,  -84.5555),
    ("MN", "Saint Paul",     "Midwest",   44.9537,  -93.0900),
    ("MS", "Jackson",        "South",     32.2988,  -90.1848),
    ("MO", "Jefferson City", "Midwest",   38.5767,  -92.1735),
    ("MT", "Helena",         "West",      46.5891, -112.0391),
    ("NE", "Lincoln",        "Midwest",   40.8136,  -96.7026),
    ("NV", "Carson City",    "West",      39.1638, -119.7674),
    ("NH", "Concord",        "Northeast", 43.2081,  -71.5376),
    ("NJ", "Trenton",        "Northeast", 40.2206,  -74.7597),
    ("NM", "Santa Fe",       "West",      35.6870, -105.9378),
    ("NY", "Albany",         "Northeast", 42.6526,  -73.7562),
    ("NC", "Raleigh",        "South",     35.7796,  -78.6382),
    ("ND", "Bismarck",       "Midwest",   46.8083, -100.7837),
    ("OH", "Columbus",       "Midwest",   39.9612,  -82.9988),
    ("OK", "Oklahoma City",  "South",     35.4676,  -97.5164),
    ("OR", "Salem",          "West",      44.9429, -123.0351),
    ("PA", "Harrisburg",     "Northeast", 40.2732,  -76.8867),
    ("RI", "Providence",     "Northeast", 41.8240,  -71.4128),
    ("SC", "Columbia",       "South",     34.0007,  -81.0348),
    ("SD", "Pierre",         "Midwest",   44.3683, -100.3510),
    ("TN", "Nashville",      "South",     36.1627,  -86.7816),
    ("TX", "Austin",         "South",     30.2672,  -97.7431),
    ("UT", "Salt Lake City", "West",      40.7608, -111.8910),
    ("VT", "Montpelier",     "Northeast", 44.2601,  -72.5754),
    ("VA", "Richmond",       "South",     37.5407,  -77.4360),
    ("WA", "Olympia",        "West",      47.0379, -122.9007),
    ("WV", "Charleston",     "South",     38.3498,  -81.6326),
    ("WI", "Madison",        "Midwest",   43.0731,  -89.4012),
    ("WY", "Cheyenne",       "West",      41.1400, -104.8202),
]


async def main() -> None:
    db = Database()
    weather = WeatherClient(base_url=OPEN_METEO_ARCHIVE_URL, path="/archive", timeout=60)
    await db.setup()
    await db.clear_extreme_days()  # thresholds may have changed; rebuild from scratch

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=365 * WINDOW_YEARS)

    for state, capital, region, lat, lon in HUBS:
        hub = Hub(id=state, name=capital, region=region, state=state, lat=lat, lon=lon)
        await db.add_hub(hub)

        while True:
            try:
                days = await weather.get_weather(hub, start, end)
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 429:
                    raise
                # a 3-year request counts as ~78 calls; the free tier allows 600 per minute
                print(f"{state}: rate limited, waiting 60s")
                await asyncio.sleep(60)

        extremes = [e for day in days if (e := is_extreme(day))]
        for e in extremes:
            await db.add_extreme_day(e)

        score = calculate(hub.id, await db.get_extreme_days(hub.id))
        await db.change_score(hub.id, score, "initial seed from archive")
        print(f"{state}: {len(extremes)} extreme days, score {score.score:.2f}")

    await weather.close()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
