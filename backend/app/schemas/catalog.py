from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class Series(BaseModel):
    name: str
    sets: Optional[List[str]] = None


class RegionSets(BaseModel):
    series: List[Series]


class PokemonSets(BaseModel):
    english: RegionSets
    korean: RegionSets


class PokemonCatalog(BaseModel):
    rarities: List[str]
    languages: List[str]
    sets: PokemonSets
    sets_flat: List[str]


class CatalogDocument(BaseModel):
    key: str  # e.g., "pokemon"
    data: Dict[str, Any]
