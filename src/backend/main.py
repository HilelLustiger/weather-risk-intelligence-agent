from dotenv import load_dotenv

load_dotenv()  # must run before the imports below: db.py reads env vars at import time

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import router
from agent import Deps
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

    yield {"deps": deps}  # -> request.state.deps

    await deps.db.close()
    await deps.weather.close()
    await deps.archive.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)

