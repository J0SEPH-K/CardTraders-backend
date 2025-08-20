from sqlalchemy import Column, String, Integer, Boolean, Float
from ..db import Base
from uuid import uuid4


class Listing(Base):
    __tablename__ = "listings"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid4()))
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    category = Column(String, nullable=False)
    sport = Column(String, nullable=True)
    year = Column(Integer, nullable=True)
    base = Column(String, nullable=True)
    card_type = Column(String, nullable=True)
    set_name = Column(String, nullable=True)
    grade = Column(String, nullable=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    price = Column(Float, nullable=True)
