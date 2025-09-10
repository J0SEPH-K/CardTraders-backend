from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List, Any
from ..mongo import get_mongo_db, mongo_enabled

router = APIRouter()

# Complete quality rating data as provided by user
QUALITY_RATINGS = {
    "PSA": {
        "name": "Professional Sports Authenticator",
        "ratings": [
            {"code": "AA", "name": "AUTHENTIC ALTERED"},
            {"code": "N0", "name": "AUTHENTIC"},
            {"code": "PSA 1", "name": "POOR"},
            {"code": "PSA 1.5", "name": "FAIR"},
            {"code": "PSA 2", "name": "GOOD"},
            {"code": "PSA 2.5", "name": "GOOD +"},
            {"code": "PSA 3", "name": "VG"},
            {"code": "PSA 3.5", "name": "VG +"},
            {"code": "PSA 4", "name": "VG-EX"},
            {"code": "PSA 4.5", "name": "VG-EX +"},
            {"code": "PSA 5", "name": "EX"},
            {"code": "PSA 5.5", "name": "EX +"},
            {"code": "PSA 6", "name": "EX-MT"},
            {"code": "PSA 6.5", "name": "EX-MT +"},
            {"code": "PSA 7", "name": "NM"},
            {"code": "PSA 7.5", "name": "NM +"},
            {"code": "PSA 8", "name": "NM-MT"},
            {"code": "PSA 8.5", "name": "NM-MT +"},
            {"code": "PSA 9", "name": "MINT"},
            {"code": "PSA 10", "name": "GEM MINT"}
        ]
    },
    "BGS": {
        "name": "Beckett Grading Services",
        "ratings": [
            {"code": "BGS 1", "name": "POOR"},
            {"code": "BGS 1.5", "name": "FAIR"},
            {"code": "BGS 2", "name": "GOOD"},
            {"code": "BGS 2.5", "name": "GOOD +"},
            {"code": "BGS 3", "name": "VG"},
            {"code": "BGS 3.5", "name": "VG +"},
            {"code": "BGS 4", "name": "VG-EX"},
            {"code": "BGS 4.5", "name": "VG-EX +"},
            {"code": "BGS 5", "name": "EXCELLENT"},
            {"code": "BGS 5.5", "name": "EXCELLENT +"},
            {"code": "BGS 6", "name": "EX-MT"},
            {"code": "BGS 6.5", "name": "EX-MT +"},
            {"code": "BGS 7", "name": "NEAR MINT"},
            {"code": "BGS 7.5", "name": "NEAR MINT +"},
            {"code": "BGS 8", "name": "NM-MT"},
            {"code": "BGS 8.5", "name": "NM-MT"},
            {"code": "BGS 9", "name": "MINT"},
            {"code": "BGS 9.5", "name": "GEM MINT"},
            {"code": "BGS 10", "name": "PRISTINE"},
            {"code": "BGS 10", "name": "BLACK LABEL PRISTINE"}
        ]
    },
    "BRG": {
        "name": "Beckett Grading Services",
        "ratings": [
            {"code": "BRG 1", "name": "FAIR"},
            {"code": "BRG 2", "name": "GOOD"},
            {"code": "BRG 3", "name": "VG"},
            {"code": "BRG 4", "name": "VG-EX"},
            {"code": "BRG 5", "name": "EX"},
            {"code": "BRG 6", "name": "EX-NM"},
            {"code": "BRG 7", "name": "NM"},
            {"code": "BRG 8", "name": "NM-MT"},
            {"code": "BRG 8.5", "name": "NM-MT +"},
            {"code": "BRG 9", "name": "MINT"},
            {"code": "BRG 10", "name": "GEM MINT"}
        ]
    },
    "ARS": {
        "name": "Arsales",
        "ratings": [
            {"code": "ARS 1", "name": "ARS 1"},
            {"code": "ARS 2", "name": "ARS 2"},
            {"code": "ARS 3", "name": "ARS 3"},
            {"code": "ARS 4", "name": "ARS 4"},
            {"code": "ARS 5", "name": "ARS 5"},
            {"code": "ARS 6", "name": "ARS 6"},
            {"code": "ARS 7", "name": "ARS 7"},
            {"code": "ARS 8", "name": "ARS 8"},
            {"code": "ARS 9", "name": "ARS 9"},
            {"code": "ARS 10", "name": "ARS 10"},
            {"code": "ARS 10 +", "name": "ARS 10 +"}
        ]
    },
    "CGC": {
        "name": "Certified Guarantee Company",
        "ratings": [
            {"code": "CGC 0.5", "name": "POOR"},
            {"code": "CGC 1", "name": "FAIR"},
            {"code": "CGC 1.5", "name": "FA-G"},
            {"code": "CGC 1.8", "name": "GOOD -"},
            {"code": "CGC 2", "name": "GOOD"},
            {"code": "CGC 2.5", "name": "GOOD +"},
            {"code": "CGC 3", "name": "G-VG"},
            {"code": "CGC 3.5", "name": "VG -"},
            {"code": "CGC 4", "name": "VG"},
            {"code": "CGC 4.5", "name": "VG +"},
            {"code": "CGC 5", "name": "VG-FN"},
            {"code": "CGC 5.5", "name": "FN -"},
            {"code": "CGC 6", "name": "FN"},
            {"code": "CGC 6.5", "name": "FN +"},
            {"code": "CGC 7", "name": "FN-VF"},
            {"code": "CGC 7.5", "name": "VF -"},
            {"code": "CGC 8", "name": "VF"},
            {"code": "CGC 8.5", "name": "VF +"},
            {"code": "CGC 9", "name": "VF-NM"},
            {"code": "CGC 9.2", "name": "NM -"},
            {"code": "CGC 9.4", "name": "NM"},
            {"code": "CGC 9.6", "name": "NM +"},
            {"code": "CGC 9.8", "name": "NM-MT"},
            {"code": "CGC 9.9", "name": "MINT"},
            {"code": "CGC 10", "name": "GEM MINT"}
        ]
    },
    "SGC": {
        "name": "Sportscard Guarantee Corporation",
        "ratings": [
            {"code": "SGC 1", "name": "POOR"},
            {"code": "SGC 1.5", "name": "FAIR"},
            {"code": "SGC 2", "name": "GOOD"},
            {"code": "SGC 2.5", "name": "GOOD +"},
            {"code": "SGC 3", "name": "VG"},
            {"code": "SGC 3.5", "name": "VG +"},
            {"code": "SGC 4", "name": "VG-EX"},
            {"code": "SGC 4.5", "name": "VG-EX +"},
            {"code": "SGC 5", "name": "EX"},
            {"code": "SGC 5.5", "name": "EX +"},
            {"code": "SGC 6", "name": "EX-NM"},
            {"code": "SGC 6.5", "name": "EX-NM +"},
            {"code": "SGC 7", "name": "NM"},
            {"code": "SGC 7.5", "name": "NM +"},
            {"code": "SGC 8", "name": "NM-MT"},
            {"code": "SGC 8.5", "name": "NM-MT"},
            {"code": "SGC 9", "name": "MINT"},
            {"code": "SGC 9.5", "name": "MINT +"},
            {"code": "SGC 10 GM", "name": "GEM MINT"},
            {"code": "SGC 10 PR", "name": "PRISTINE"}
        ]
    },
    "HGA": {
        "name": "Hybrid Grading Approach",
        "ratings": [
            {"code": "HGA 1.0", "name": "POOR"},
            {"code": "HGA 1.5", "name": "FAIR"},
            {"code": "HGA 2.0", "name": "GOOD"},
            {"code": "HGA 2.5", "name": "GOOD +"},
            {"code": "HGA 3.0", "name": "VG"},
            {"code": "HGA 3.5", "name": "VG +"},
            {"code": "HGA 4.0", "name": "VG-EX"},
            {"code": "HGA 4.5", "name": "VG-EX +"},
            {"code": "HGA 5.0", "name": "EX"},
            {"code": "HGA 5.5", "name": "EX +"},
            {"code": "HGA 6.0", "name": "EX-NM"},
            {"code": "HGA 6.5", "name": "EX-NM +"},
            {"code": "HGA 7.0", "name": "NM"},
            {"code": "HGA 7.5", "name": "NM +"},
            {"code": "HGA 8.0", "name": "NM-MT"},
            {"code": "HGA 8.5", "name": "NM-MT +"},
            {"code": "HGA 9", "name": "MINT"},
            {"code": "HGA 10 GM", "name": "GEM MT"},
            {"code": "HGA 10 FL", "name": "FLAWLESS"}
        ]
    }
}

@router.get("/")
@router.get("")
async def get_quality_ratings(mdb=Depends(get_mongo_db)) -> Dict[str, Any]:
    """Get all quality rating scales"""
    if not mongo_enabled() or mdb is None:
        return {"quality_ratings": QUALITY_RATINGS}
    
    # Try to get from database, fallback to static data
    try:
        collection = mdb["qualityRatings"]
        db_ratings = await collection.find_one({"_id": "quality_ratings"})
        if db_ratings and "data" in db_ratings:
            return {"quality_ratings": db_ratings["data"]}
    except Exception:
        pass
    
    return {"quality_ratings": QUALITY_RATINGS}

@router.get("/scales")
async def get_rating_scales(mdb=Depends(get_mongo_db)) -> List[str]:
    """Get list of available rating scales"""
    data = await get_quality_ratings(mdb)
    return list(data["quality_ratings"].keys())

@router.get("/scales/{scale}")
async def get_scale_ratings(scale: str, mdb=Depends(get_mongo_db)) -> Dict[str, Any]:
    """Get ratings for a specific scale"""
    data = await get_quality_ratings(mdb)
    scale_upper = scale.upper()
    
    if scale_upper not in data["quality_ratings"]:
        raise HTTPException(status_code=404, detail=f"Rating scale '{scale}' not found")
    
    return data["quality_ratings"][scale_upper]

@router.post("/initialize")
async def initialize_quality_ratings(mdb=Depends(get_mongo_db)) -> Dict[str, str]:
    """Initialize quality ratings collection in database"""
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Requires MongoDB")
    
    try:
        collection = mdb["qualityRatings"]
        await collection.replace_one(
            {"_id": "quality_ratings"},
            {"_id": "quality_ratings", "data": QUALITY_RATINGS},
            upsert=True
        )
        return {"message": "Quality ratings initialized successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to initialize quality ratings: {str(e)}")
