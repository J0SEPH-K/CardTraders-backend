import os
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "cardtraders")
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "listings")

# Allow disabling Mongo for local/dev runs by setting MONGO_ENABLED=false
_MONGO_ENABLED = os.getenv("MONGO_ENABLED", "true").lower() in {"1", "true", "yes"}

_mongo_client: Optional[AsyncIOMotorClient] = (
    AsyncIOMotorClient(MONGODB_URI) if (MONGODB_URI and _MONGO_ENABLED) else None
)


def mongo_enabled() -> bool:
    return _mongo_client is not None


async def get_mongo_db() -> Optional[AsyncIOMotorDatabase]:
    if _mongo_client is None:
        return None
    return _mongo_client[MONGODB_DB_NAME]
