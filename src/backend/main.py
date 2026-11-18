from dotenv import load_dotenv

load_dotenv()

import logfire

logfire.configure(service_name="weather-risk-backend", send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()
logfire.instrument_httpx()

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import router
from agent import Deps
from daily_update import run_daily
from db import Database
from weather_client import OPEN_METEO_ARCHIVE_URL, WeatherClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    deps = Deps(
        db=Database(),
        weather=WeatherClient(),
        archive=WeatherClient(base_url=OPEN_METEO_ARCHIVE_URL, path="/archive", timeout=30),
    )
    await deps.db.setup()
    daily = asyncio.create_task(run_daily(deps.db, deps.weather))

    yield {"deps": deps}  # -> request.state.deps

    daily.cancel()
    await deps.db.close()
    await deps.weather.close()
    await deps.archive.close()


app = FastAPI(lifespan=lifespan)
logfire.instrument_fastapi(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)

