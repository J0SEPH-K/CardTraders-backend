import logging
from fastapi import FastAPI
from sqlalchemy import text
from .routers import health, listings, catalog
from .db import Base, engine
from . import models  # noqa: F401
from .mongo import mongo_enabled, get_mongo_db

app = FastAPI(title="CardTraders API")
logger = logging.getLogger("uvicorn.error")

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(listings.router, prefix="/listings", tags=["listings"])
app.include_router(catalog.router, prefix="/catalog", tags=["catalog"])

# Create tables & log DB connectivity on startup (simple dev setup; use Alembic in prod)
@app.on_event("startup")
async def on_startup():
	# Ensure SQL tables exist (safe no-op for Mongo-only usage)
	try:
		Base.metadata.create_all(bind=engine)
	except Exception as e:
		logger.warning("SQL table creation skipped/failed: %s", e)

	# Prefer Mongo if configured, else probe SQL
	if mongo_enabled():
		try:
			mdb = await get_mongo_db()
			if mdb is None:
				raise RuntimeError("Mongo client not available")
			await mdb.command("ping")
			logger.info("Database connected: MongoDB")
			return
		except Exception as e:
			logger.warning("MongoDB ping failed: %s", e)

	# Probe SQLAlchemy engine
	try:
		with engine.connect() as conn:
			conn.execute(text("SELECT 1"))
		logger.info("Database connected: SQLAlchemy engine OK")
	except Exception as e:
		logger.warning("No database connection established; using in-memory fallback. Detail: %s", e)
