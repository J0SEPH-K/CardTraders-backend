from fastapi import FastAPI
from .routers import health, listings

app = FastAPI(title="CardTraders API")

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(listings.router, prefix="/listings", tags=["listings"])
