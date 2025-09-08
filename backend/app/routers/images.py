from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from bson import ObjectId
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from ..mongo import get_mongo_db, mongo_enabled
import os
from pathlib import Path

router = APIRouter()


@router.get("/{image_id}")
async def get_image(image_id: str, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Requires MongoDB")
    try:
        oid = ObjectId(image_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image id")

    bucket = AsyncIOMotorGridFSBucket(mdb, bucket_name="uploads")
    # Read file metadata to get contentType
    files_coll = mdb["uploads.files"]
    meta = await files_coll.find_one({"_id": oid})
    if not meta:
        raise HTTPException(status_code=404, detail="Not found")
    content_type: Optional[str] = None
    md = meta.get("metadata") or {}
    if isinstance(md, dict):
        content_type = md.get("contentType")

    stream = await bucket.open_download_stream(oid)
    try:
        data = await stream.read()
    finally:
        # GridOut.close() is synchronous; do not await
        try:
            stream.close()
        except Exception:
            pass
    return StreamingResponse(iter([data]), media_type=content_type or "application/octet-stream")


@router.get("/local/uploads/{filename}")
async def get_local_image(filename: str):
    # Dev fallback: serve files saved on disk by uploaded_cards
    media_root = os.getenv("MEDIA_ROOT") or str(Path(__file__).resolve().parents[2] / "media")
    fpath = Path(media_root) / "uploads" / filename
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="Not found")
    # naive content type based on extension
    ext = fpath.suffix.lower()
    ctype = "image/jpeg"
    if ext == ".png":
        ctype = "image/png"
    return FileResponse(path=str(fpath), media_type=ctype)
