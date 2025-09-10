from fastapi import APIRouter, Depends
from ..mongo import get_mongo_db
from ..config import get_public_config

router = APIRouter()


@router.get("/")
async def read_public_config(mdb=Depends(get_mongo_db)):
    cfg = await get_public_config(mdb)
    return {"config": cfg}

# Also respond to '/config' (no trailing slash) to avoid 307s and proxy issues
@router.get("")
async def read_public_config_no_slash(mdb=Depends(get_mongo_db)):
    cfg = await get_public_config(mdb)
    return {"config": cfg}
