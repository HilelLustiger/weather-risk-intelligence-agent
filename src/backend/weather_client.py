from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from models import DailyWeather, Hub

OPEN_METEO_BASE_URL = "https://api.open-meteo.com/v1"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1"

# The fields of DailyWeather that come from the API (everything except hub_id and date).
DAILY_VARIABLES = [f for f in DailyWeather.model_fields if f not in ("hub_id", "date")]


class WeatherClient:
    """Holds one HTTP connection pool to Open-Meteo. Create once, `await close()` on shutdown."""

    def __init__(self, base_url: str = OPEN_METEO_BASE_URL, path: str = "/forecast", timeout: float = 10.0) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._path = path

    async def close(self) -> None:
        await self._http.aclose()

    async def get_weather(self, hub: Hub, start: date, end: date) -> list[DailyWeather]:
        """Daily forecast for a hub, from start to end (inclusive).
        Open-Meteo's forecast covers roughly the past 3 months to 16 days ahead."""
        params = {
            "latitude": hub.lat,
            "longitude": hub.lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join(DAILY_VARIABLES),
            "timezone": "auto",  # days are in the hub's local time
            # Open-Meteo defaults are metric: °C, km/h, mm (snowfall in cm)
        }
        response = await self._http.get(self._path, params=params)
        if response.status_code == 400:
            # Open-Meteo explains bad requests (e.g. date out of range) in "reason"
            raise ValueError(f"Open-Meteo: {response.json().get('reason', response.text)}")
        response.raise_for_status()
        return self._parse(hub.id, response.json())

    def _parse(self, hub_id: str, payload: dict[str, Any]) -> list[DailyWeather]:
        """Turn Open-Meteo's column format into one DailyWeather per day.

        The API returns {"daily": {"time": [d1, d2, ...], "rain_sum": [v1, v2, ...], ...}},
        i.e. one list per variable, all aligned by index."""
        daily = payload["daily"]
        return [
            DailyWeather(
                hub_id=hub_id,
                date=day,
                **{var: daily[var][i] for var in DAILY_VARIABLES},
            )
            for i, day in enumerate(daily["time"])
        ]
