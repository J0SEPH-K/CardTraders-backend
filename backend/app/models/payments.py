from sqlalchemy import Column, String, Float, DateTime, Text
from ..db import Base
from uuid import uuid4
from datetime import datetime


class Payment(Base):
    __tablename__ = "payments"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid4()))
    buyer_id = Column(String, nullable=False)
    seller_id = Column(String, nullable=False)
    item_id = Column(String, nullable=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False, default="KRW")
    status = Column(String, nullable=False, default="PENDING")  # PENDING, PAID, REFUNDED
    provider_payment_id = Column(String, nullable=True)
    provider_raw = Column(Text, nullable=True)
    payment_reference = Column(String, nullable=True, index=True)
    proof_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Wallet(Base):
    __tablename__ = "wallets"

    user_id = Column(String, primary_key=True, index=True)
    balance = Column(Float, nullable=False, default=0.0)


class Ledger(Base):
    __tablename__ = "ledger"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid4()))
    user_id = Column(String, nullable=False)
    change = Column(Float, nullable=False)
    reason = Column(String, nullable=True)
    related_payment_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid4()))
    provider = Column(String, nullable=False)
    provider_event_id = Column(String, nullable=False, unique=True, index=True)
    processed_at = Column(DateTime, default=datetime.utcnow)
    raw = Column(Text, nullable=True)
