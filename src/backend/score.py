from datetime import datetime, timedelta, timezone

from models import DailyWeather, Score, ExtremeDay

THRESHOLDS = {
    "low temperature":  lambda d: (d.temperature_2m_min or 0) <= -5,
    "high temperature": lambda d: (d.temperature_2m_max or 0) >= 38,
    "wind speed":       lambda d: (d.wind_speed_10m_max or 0) >= 65,
    "wind gusts":       lambda d: (d.wind_gusts_10m_max or 0) >= 80,
    "rain":             lambda d: (d.precipitation_sum or 0) >= 50,
    "snow":             lambda d: (d.snowfall_sum or 0) >= 5,
}

WEIGHTS = {
    "low temperature":  1.5,
    "high temperature": 1,
    "wind speed":       1.5,
    "wind gusts":       2,
    "rain":             1.5,
    "snow":             2,
}

# Inferred weather types: a combination of measurements on the same day. Each part may be
# below its own extreme threshold. Reported only - not part of the score.
WEATHER_TYPES = {
    "storm": lambda d: (d.wind_gusts_10m_max or 0) >= 60 and (d.precipitation_sum or 0) >= 20,
    "fire weather": lambda d: (d.wind_gusts_10m_max or 0) >= 35 and (d.temperature_2m_max or 0) >= 36,
    "ice storm": lambda d: (d.snowfall_sum or 0) >= 6 and (d.precipitation_sum or 0) >= 20,
}

WINDOW_YEARS = 3

def is_extreme(day: DailyWeather) -> ExtremeDay | None:
    components = [c for c, crossed in THRESHOLDS.items() if crossed(day)]
    weather_types = [t for t, matches in WEATHER_TYPES.items() if matches(day)]
    if not components and not weather_types:
        return None
    return ExtremeDay(hub_id=day.hub_id, day=day.date, components=components, weather_types=weather_types)

def _in_window(days: list[ExtremeDay]) -> list[ExtremeDay]:
    start = (datetime.now(timezone.utc) - timedelta(days=365 * WINDOW_YEARS)).date()
    return [d for d in days if d.day >= start]

def calculate(hub_id: str, days: list[ExtremeDay]) -> Score:
    score = sum(WEIGHTS[c] for d in _in_window(days) for c in d.components) / WINDOW_YEARS
    return Score(hub_id=hub_id, computed_at=datetime.now(timezone.utc), score=score)

def component_counts(days: list[ExtremeDay]) -> dict[str, int]:
    """Number of extreme days per component in the scoring window (the same days `calculate` uses)."""
    return {c: sum(c in d.components for d in _in_window(days)) for c in WEIGHTS}

def daily_summary(days: list[DailyWeather]) -> dict[str, float]:
    """Counts ordinary (not only extreme) weather days in a list of daily reports."""
    return {
        "days": len(days),
        "snow_days (snowfall > 0 cm)": sum((d.snowfall_sum or 0) > 0 for d in days),
        "rain_days (precipitation >= 1 mm)": sum((d.precipitation_sum or 0) >= 1 for d in days),
        "frost_days (min < 0°C)": sum((d.temperature_2m_min or 0) < 0 for d in days),
        "hot_days (max >= 30°C)": sum((d.temperature_2m_max or 0) >= 30 for d in days),
        "total_snowfall_cm": round(sum(d.snowfall_sum or 0 for d in days), 1),
        "total_precipitation_mm": round(sum(d.precipitation_sum or 0 for d in days), 1),
    }

def weather_type_counts(days: list[ExtremeDay]) -> dict[str, int]:
    """Number of days per inferred weather type in the scoring window."""
    return {t: sum(t in d.weather_types for d in _in_window(days)) for t in WEATHER_TYPES}

def normalize(scores: list[Score]) -> list[Score]:
    """Rescales scores to 1-100: the lowest hub gets 1, the highest gets 100."""
    if not scores:
        return []
    lo = min(s.score for s in scores)
    hi = max(s.score for s in scores)
    if hi == lo:
        return [s.model_copy(update={"score": 100.0}) for s in scores]
    return [s.model_copy(update={"score": 1 + 99 * (s.score - lo) / (hi - lo)}) for s in scores]