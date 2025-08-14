from fastapi import APIRouter
from typing import List
from ..schemas.listings import Listing, ListingCreate

router = APIRouter()

# in-memory stub
_DATA: List[Listing] = []

@router.get("/", response_model=List[Listing])
def list_listings():
    return _DATA

@router.post("/", response_model=Listing)
def create_listing(payload: ListingCreate):
    item = Listing(id=str(len(_DATA)+1), **payload.model_dump())
    _DATA.append(item)
    return item
