from __future__ import annotations

import os
from datetime import datetime, timezone

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument

from models import ConversationSummary, JobRun, Score, ScoreChange, Hub, ExtremeDay

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "weather_risk")

# Collections:
#   hubs           - one doc per hub; the hub's current score is embedded under "score"
#   score_changes  - append-only history, one doc per score update
#   conversations  - {"_id": conversation_id, "messages": [...]}
#   extreme_days   - one doc per (hub_id, day); day stored as "YYYY-MM-DD"


class Database:
    def __init__(self, uri: str = MONGODB_URI):
        # tz_aware so datetimes come back with UTC tzinfo instead of naive
        self.client = AsyncMongoClient(uri, tz_aware=True)
        # uses the db name in the URI if present, else MONGODB_DB_NAME
        db = self.client.get_default_database(default=MONGODB_DB_NAME)
        self.hubs = db.hubs
        self.score_changes = db.score_changes
        self.conversations = db.conversations
        self.extreme_days = db.extreme_days
        self.job_runs = db.job_runs


    async def setup(self) -> None:
        """Create indexes. Safe to call on every startup (no-op if they exist)."""
        await self.hubs.create_index("id", unique=True)
        await self.hubs.create_index("region")
        await self.hubs.create_index([("score.score", DESCENDING)])
        await self.score_changes.create_index(
            [("hub_id", ASCENDING), ("changed_at", DESCENDING)]
        )
        await self.extreme_days.create_index(
            [("hub_id", ASCENDING), ("day", ASCENDING)], unique=True
        )
        await self.job_runs.create_index([("status", ASCENDING), ("run_at", DESCENDING)])

    async def add_job_run(self, run: JobRun) -> None:
        """saves one run of the daily update (dates stored as ISO strings, like extreme days)"""
        await self.job_runs.insert_one(run.model_dump(mode="json"))

    async def get_last_successful_run(self) -> JobRun | None:
        """returns the most recent successful daily update, or None if there was none"""
        doc = await self.job_runs.find_one({"status": "success"}, {"_id": 0}, sort=[("run_at", DESCENDING)])
        return JobRun.model_validate(doc) if doc else None

    async def close(self) -> None:
        await self.client.close()

    async def get_hubs(self) -> list[Hub]:
        """returns all hubs"""
        return [Hub.model_validate(doc) async for doc in self.hubs.find({})]

    async def add_hub(self, hub: Hub) -> None:
        """saves a hub; if it already exists its details are updated and its score is kept"""
        await self.hubs.update_one({"id": hub.id}, {"$set": hub.model_dump(exclude={"score"})}, upsert=True)

    async def get_extreme_days(self, hub_id: str) -> list[ExtremeDay]:
        """returns the exterme days of a given hub"""
        cursor = self.extreme_days.find({"hub_id": hub_id}, {"_id": 0}).sort("day", ASCENDING)
        return [ExtremeDay.model_validate(doc) async for doc in cursor]

    async def clear_extreme_days(self) -> None:
        """deletes all extreme days (used before a reseed, e.g. after thresholds change)"""
        await self.extreme_days.delete_many({})

    async def add_extreme_day(self, day: ExtremeDay) -> None:
        """saves an extreme day; if that hub+day already exists it is overwritten (no duplicates)"""
        # mode="json" turns `day` into "YYYY-MM-DD": BSON can't store a plain date,
        # and ISO strings still sort chronologically.
        doc = day.model_dump(mode="json")
        await self.extreme_days.replace_one(
            {"hub_id": doc["hub_id"], "day": doc["day"]}, doc, upsert=True
        )
    
    async def get_score(self, hub_id: str) -> Score | None:
        """returns the current score of a given hub, or None if the hub has no score yet"""
        doc = await self.hubs.find_one({"id": hub_id}, {"_id": 0, "score": 1})
        if not doc or not doc.get("score"):
            return None
        return Score.model_validate(doc["score"])

    async def change_score(self, hub_id: str, new_score: Score, reasons: str) -> ScoreChange:
        """updates the score of the given hub and creates a new ScoreChange in the score_changes collection"""
        # find_one_and_update returns the doc *before* the update, so reading the old
        # score and writing the new one is a single atomic operation.
        before = await self.hubs.find_one_and_update(
            {"id": hub_id},
            {"$set": {"score": new_score.model_dump()}},
            projection={"_id": 0, "score": 1},
            return_document=ReturnDocument.BEFORE,
        )
        if before is None:
            raise KeyError(f"hub {hub_id!r} not found")

        old = before.get("score")
        change = ScoreChange(
            hub_id=hub_id,
            changed_at=datetime.now(timezone.utc),
            last_score=Score.model_validate(old) if old else None,
            reasons=reasons,
        )
        await self.score_changes.insert_one(change.model_dump())
        return change

    async def get_top_k(self, k: int, region: str | None = None) -> list[Hub]:
        """returns the K hubs with the highest score, optionally only from one region"""
        query = {"score": {"$ne": None}} | ({"region": region} if region else {})
        cursor = self.hubs.find(query).sort("score.score", DESCENDING).limit(k)
        return [Hub.model_validate(doc) async for doc in cursor]

    async def save_conversation(self, conversation_id: str, messages: list[ModelMessage], title: str = "") -> None:
        """appends messages to the conversation, creating it if needed.
        Pass `result.new_messages()` so earlier turns aren't stored twice.
        `title` is only stored when the conversation is created."""
        await self.conversations.update_one(
            {"_id": conversation_id},
            {
                "$push": {"messages": {"$each": ModelMessagesTypeAdapter.dump_python(messages, mode="json")}},
                "$set": {"updated_at": datetime.now(timezone.utc)},
                "$setOnInsert": {"title": title},
            },
            upsert=True,
        )

    async def list_conversations(self) -> list[ConversationSummary]:
        """returns all conversations (without messages), most recently updated first"""
        cursor = self.conversations.find({}, {"title": 1, "updated_at": 1}).sort("updated_at", DESCENDING)
        return [
            ConversationSummary(id=doc["_id"], title=doc.get("title") or "Untitled", updated_at=doc.get("updated_at"))
            async for doc in cursor
        ]

    async def load_conversation(self, conversation_id: str) -> list[ModelMessage]:
        """loads the conversation with the matching id ([] if it doesn't exist)"""
        doc = await self.conversations.find_one({"_id": conversation_id})
        if not doc:
            return []
        return ModelMessagesTypeAdapter.validate_python(doc["messages"])
