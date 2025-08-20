from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..db import get_db
from ..mongo import get_mongo_db, mongo_enabled

router = APIRouter()


@router.get("/")
def root():
    return {"status": "ok"}


@router.get("/db")
async def db_health(db: Session = Depends(get_db), mdb=Depends(get_mongo_db)):
    # Prefer MongoDB if configured
    if mongo_enabled() and mdb is not None:
        try:
            await mdb.command("ping")
            return {"status": "ok", "database": "mongo"}
        except Exception as e:
            return {"status": "error", "database": "mongo", "detail": str(e)}

    # Fallback to SQLAlchemy
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "sql"}
    except Exception as e:
        return {"status": "degraded", "database": "memory", "detail": str(e)}
