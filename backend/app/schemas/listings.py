from pydantic import BaseModel
from typing import Literal, Optional

Category = Literal["sports","yugioh","pokemon","idol"]

class ListingBase(BaseModel):
    title: str
    description: Optional[str] = None
    category: Category
    sport: Optional[str] = None
    year: Optional[int] = None
    base: Optional[str] = None
    card_type: Optional[str] = None
    set_name: Optional[str] = None
    grade: Optional[str] = None
    is_verified: bool = False
    price: Optional[float] = None

class ListingCreate(ListingBase):
    pass

class Listing(ListingBase):
    id: str
