import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from .routers import health, listings, catalog, auth
from .routers import uploaded_cards
from .routers import images
from .routers import tcgdex
from .routers import chats
from .routers import payments
from .routers import quality_ratings
from .db import Base, engine
from . import models  # noqa: F401
from .mongo import mongo_enabled, get_mongo_db
from .routers import config as config_router
from .config import load_server_config_from_mongo

app = FastAPI(title="CardTraders API")
logger = logging.getLogger("uvicorn.error")

# Dev CORS (adjust origins for production)
app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(listings.router, prefix="/listings", tags=["listings"])
app.include_router(catalog.router, prefix="/catalog", tags=["catalog"])
app.include_router(tcgdex.router, prefix="/tcgdx", tags=["tcgdx"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(uploaded_cards.router, prefix="/uploaded-cards", tags=["uploaded-cards"])
app.include_router(images.router, prefix="/images", tags=["images"])
app.include_router(chats.router, prefix="/chats", tags=["chats"])
app.include_router(payments.router, prefix="/payments", tags=["payments"])
app.include_router(quality_ratings.router, prefix="/quality-ratings", tags=["quality-ratings"])
app.include_router(config_router.router, prefix="/config", tags=["config"]) 

# Create tables & log DB connectivity on startup (simple dev setup; use Alembic in prod)
@app.on_event("startup")
async def on_startup():
	# Ensure SQL tables exist (safe no-op for Mongo-only usage)
	try:
		Base.metadata.create_all(bind=engine)
	except Exception as e:
		logger.warning("SQL table creation skipped/failed: %s", e)

	# Lightweight runtime migration: for simple dev SQLite DBs, add newly introduced
	# columns if they are missing. This keeps local sqlite ./app.db usable without
	# requiring a full migration tool during quick development iterations.
	try:
		# Only run ALTER TABLE flow for sqlite to avoid touching production DBs.
		if getattr(engine, "dialect", None) and engine.dialect.name == "sqlite":
			from sqlalchemy import text as _text
			with engine.begin() as conn:
				rows = conn.execute(_text("PRAGMA table_info(payments)")).mappings().all()
				existing = {r["name"] for r in rows}
				added = []
				if "payment_reference" not in existing:
					conn.execute(_text("ALTER TABLE payments ADD COLUMN payment_reference VARCHAR"))
					added.append("payment_reference")
				if "proof_url" not in existing:
					conn.execute(_text("ALTER TABLE payments ADD COLUMN proof_url VARCHAR"))
					added.append("proof_url")
				if added:
					logger.info("Added missing payments columns: %s", added)
	except Exception as e:
		logger.warning("Runtime migration check failed: %s", e)

	# Prefer Mongo if configured, else probe SQL
	if mongo_enabled():
		try:
			mdb = await get_mongo_db()
			if mdb is None:
				raise RuntimeError("Mongo client not available")
			await mdb.command("ping")
			# Load server config from Mongo at startup
			try:
				await load_server_config_from_mongo(mdb)
			except Exception as ce:
				logger.warning("Loading server config failed: %s", ce)
			# Ensure chat indexes
			try:
				from .routers.chats import ensure_indexes
				await ensure_indexes(mdb)
			except Exception as ie:
				logger.warning("Chat index creation failed: %s", ie)
			logger.info("Database connected: MongoDB")
			return
		except Exception as e:
			logger.warning("MongoDB ping failed: %s", e)

	# Probe SQLAlchemy engine
	try:
		with engine.begin() as c:
			c.execute(text("SELECT 1"))
		logger.info("Database connected: SQL")
	except Exception as e:
		logger.warning("SQL DB connection failed: %s", e)


@app.get("/")
def read_root():
	return {"message": "CardTraders API is running"}


if __name__ == "__main__":
	import uvicorn
	uvicorn.run(app, host="0.0.0.0", port=8000)
