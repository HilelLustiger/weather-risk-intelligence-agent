from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field

Region = Literal["Northeast", "Midwest", "South", "West"]
WeatherComponents = Literal["low temperature", "high temperature", "wind speed", "wind gusts", "rain", "snow"]
WeatherType = Literal["storm", "fire weather", "ice storm"]  # inferred from a combination of measurements; reported, not scored

## General types
## the score of each hub
class Score(BaseModel):
    hub_id: str
    computed_at: datetime
    score: float

class Hub(BaseModel):
    id: str
    name: str
    region: Region
    state: str
    lat: float
    lon: float
    score: Score | None = None  # None until the first score is computed

class ScoreChange(BaseModel):
    hub_id: str
    changed_at: datetime
    last_score: Score | None  # None on the hub's first score
    new_score: Score | None = None  # None only on records saved before this field existed
    reasons: str

## One run of the daily score update
class JobRun(BaseModel):
    id: str
    run_at: datetime
    status: Literal["success", "failure"]
    start_date: date  # first day fetched
    end_date: date    # last day fetched; the next run starts the day after the last success
    changed_hubs: list[str] = []
    error: str | None = None

class ExtremeDay(BaseModel):
    hub_id: str
    day: date
    components: list[WeatherComponents]
    weather_types: list[WeatherType] = []

## Type for the returning data from the weather API
## One day of forecast for one hub. Units: °C, km/h, mm (snowfall_sum in cm), %.
## Any value can be None if Open-Meteo has no data for that day.
class DailyWeather(BaseModel):
    hub_id: str
    date: date
    weather_code: int | None
    temperature_2m_max: float | None
    temperature_2m_min: float | None
    wind_speed_10m_max: float | None
    wind_gusts_10m_max: float | None
    rain_sum: float | None
    showers_sum: float | None
    snowfall_sum: float | None
    precipitation_sum: float | None
    precipitation_probability_max: int | None

## Types for the LLM-UI
class HubRef(BaseModel):
    hub_id: str = Field(description="2-letter state code of the hub, e.g. 'TX'")
    score: float | None = Field(default=None, description="the hub's risk score, if relevant to the answer")

class AgentAnswer(BaseModel):
    answer: str = Field(description="the full answer for the analyst, in markdown")
    hubs: list[HubRef] = Field(default_factory=list, description="hubs the answer is about, most exposed first; empty if none")
    assumptions: list[str] = Field(default_factory=list, description="caveats and limitations that apply to this answer")

## Types for the chat API
class ChatRequest(BaseModel):
    session_id: str
    message: str

class ChatResponse(BaseModel):
    session_id: str
    answer: AgentAnswer

class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: datetime | None = None

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    assumptions: list[str] = []

class ConversationDetail(BaseModel):
    id: str
    messages: list[ChatMessage]