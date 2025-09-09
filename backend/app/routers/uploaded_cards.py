from fastapi import APIRouter, Depends, HTTPException, Query
import logging
import base64
import re
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from typing import Optional, List, Any, Dict
from datetime import datetime, timezone
from pymongo import ReturnDocument
from bson.decimal128 import Decimal128  # for Decimal128 <-> int conversions
from bson import ObjectId
import os
from pathlib import Path
from ..mongo import get_mongo_db, mongo_enabled

router = APIRouter()
log = logging.getLogger("uvicorn.error")


def _normalize_uploaded_card(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of doc with JSON-safe types for Decimal128 and datetimes."""
    if not isinstance(doc, dict):
        return doc
    out = doc.copy()
    out.pop("_id", None)
    # uploadedBy Decimal128 -> int or string
    if "uploadedBy" in out and out["uploadedBy"] is not None:
        ub = out["uploadedBy"]
        try:
            if isinstance(ub, Decimal128):
                out["uploadedBy"] = int(ub.to_decimal())
            elif isinstance(ub, (int, float)):
                out["uploadedBy"] = int(ub)
            elif isinstance(ub, str) and ub.isdigit():
                out["uploadedBy"] = int(ub)
            else:
                out["uploadedBy"] = str(ub)
        except Exception:
            out["uploadedBy"] = str(ub)
    # datetimes -> ISO
    for k in ("uploadDate", "createdAt"):
        v = out.get(k)
        if isinstance(v, datetime):
            try:
                out[k] = v.astimezone(timezone.utc).isoformat()
            except Exception:
                out[k] = v.isoformat()
    return out


async def _next_sequence(mdb, name: str) -> int:
    counters = mdb["counters"]
    res = await counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(res.get("seq") or 1)


@router.get("/")
@router.get("")
async def list_uploaded_cards(
    category: Optional[str] = None,
    q: Optional[str] = None,
    uploadedBy: Optional[str] = None,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    debug_user_id: Optional[str] = Query(None, description="User ID for debug logging favorites"),
    mdb=Depends(get_mongo_db),
) -> List[Dict[str, Any]]:
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Requires MongoDB")
    
    # Debug logging: Print user favorites when debug_user_id is provided
    if debug_user_id:
        try:
            users_coll = mdb["users"]
            user = await users_coll.find_one({"userId": debug_user_id}, {"favorites": 1, "starred_item": 1, "username": 1})
            if user:
                favorites = user.get("favorites", [])
                starred_item = user.get("starred_item", [])
                username = user.get("username", "Unknown")
                print(f"=== DEBUG: User favorites for {username} (ID: {debug_user_id}) ===")
                print(f"Favorites array: {favorites}")
                print(f"Starred_item array: {starred_item}")
                print(f"Total favorites: {len(favorites) if favorites else 0}")
                print(f"Total starred_item: {len(starred_item) if starred_item else 0}")
                print("=" * 60)
            else:
                print(f"=== DEBUG: User not found for ID: {debug_user_id} ===")
        except Exception as e:
            print(f"=== DEBUG: Error fetching user favorites: {e} ===")
    
    coll = mdb["uploadedCards"]
    query: Dict[str, Any] = {}
    if category and category != "all":
        query["category"] = category
    if q:
        query["card_name"] = {"$regex": q, "$options": "i"}
    # Optional filter by uploader (supports numeric legacy IDs stored as Decimal128 or plain string userId)
    if uploadedBy:
        s = str(uploadedBy).strip()
        if s.isdigit():
            # match both Decimal128 and string
            try:
                query["$or"] = [
                    {"uploadedBy": Decimal128(s)},
                    {"uploadedBy": s},
                ]
            except Exception:
                query["uploadedBy"] = s
        else:
            query["uploadedBy"] = s
    cur = coll.find(query).sort([("id", -1)]).skip(int(offset)).limit(int(limit))
    items: List[Dict[str, Any]] = []
    user_ids: List[int] = []
    user_ids_str: List[str] = []
    user_obj_ids: List[ObjectId] = []
    async for doc in cur:
        doc.pop("_id", None)
        # normalize price to number if stored as string
        if "price" in doc and doc["price"] is not None:
            p = doc["price"]
            if isinstance(p, str):
                try:
                    doc["price"] = float(p.replace(",", "").strip())
                except Exception:
                    # leave as-is if parsing fails
                    pass
        # normalize uploadDate/createdAt to ISO string for JSON
        if "uploadDate" in doc and doc["uploadDate"] is not None:
            ud = doc["uploadDate"]
            if isinstance(ud, datetime):
                doc["uploadDate"] = ud.astimezone(timezone.utc).isoformat()
        elif "createdAt" in doc and doc["createdAt"] is not None:
            ca = doc["createdAt"]
            if isinstance(ca, datetime):
                doc["createdAt"] = ca.astimezone(timezone.utc).isoformat()

        # convert uploadedBy Decimal128 -> int for JSON
        if "uploadedBy" in doc and doc["uploadedBy"] is not None:
            ub = doc["uploadedBy"]
            try:
                if isinstance(ub, Decimal128):
                    doc["uploadedBy"] = int(ub.to_decimal())
                elif isinstance(ub, str) and ub.isdigit():
                    doc["uploadedBy"] = int(ub)
                elif isinstance(ub, (int, float)):
                    doc["uploadedBy"] = int(ub)
            except Exception:
                # best-effort: stringify if conversion fails
                doc["uploadedBy"] = str(ub)
        # collect user ids for later lookup (int or string userId)
        if isinstance(doc.get("uploadedBy"), int):
            val = int(doc["uploadedBy"])
            user_ids.append(val)
            # also track as string to match against users.userId when legacy stored as strings
            user_ids_str.append(str(val))
        elif isinstance(doc.get("uploadedBy"), str):
            s = str(doc["uploadedBy"]).strip()
            user_ids_str.append(s)
            # if it looks like an ObjectId, collect it too
            try:
                if len(s) == 24:
                    user_obj_ids.append(ObjectId(s))
            except Exception:
                pass
        items.append(doc)

    # Enrich with seller address by looking up users collection
    try:
        users_coll = mdb["users"]
        # Build user maps for int ids (legacy) and userId strings (current)
        user_map_int: Dict[int, Dict[str, Any]] = {}
        user_map_str: Dict[str, Dict[str, Any]] = {}

        if user_ids:
            uniq_ids = sorted({int(uid) for uid in user_ids})
            # Attempt legacy numeric id match if such field exists
            decimal_ids = [Decimal128(str(uid)) for uid in uniq_ids]
            q_int = {"id": {"$in": uniq_ids + decimal_ids}}
            async for u in users_coll.find(q_int, {"_id": 0, "id": 1, "address": 1, "username": 1}):
                raw_id = u.get("id")
                try:
                    if isinstance(raw_id, Decimal128):
                        user_map_int[int(raw_id.to_decimal())] = u
                    elif isinstance(raw_id, (int, float)):
                        user_map_int[int(raw_id)] = u
                    elif isinstance(raw_id, str) and raw_id.isdigit():
                        user_map_int[int(raw_id)] = u
                except Exception:
                    pass

        if user_ids_str:
            uniq_ids_str = sorted({str(uid) for uid in user_ids_str})
            q_str = {"userId": {"$in": uniq_ids_str}}
            async for u in users_coll.find(q_str, {"_id": 0, "userId": 1, "address": 1, "username": 1}):
                uid = u.get("userId")
                if isinstance(uid, str):
                    user_map_str[uid] = u

        # attach address and username if present
        for d in items:
            uid = d.get("uploadedBy")
            if isinstance(uid, int) and uid in user_map_int:
                addr = user_map_int[uid].get("address") or user_map_int[uid].get("addr") or ""
                d["seller_address"] = addr
                uname = user_map_int[uid].get("username")
                if uname:
                    d["uploadedByName"] = uname
                    d["username"] = uname
            elif isinstance(uid, int):
                # fallback: try match by userId string
                s = str(uid)
                if s in user_map_str:
                    addr = user_map_str[s].get("address") or ""
                    d["seller_address"] = addr or d.get("seller_address")
                    uname = user_map_str[s].get("username")
                    if uname:
                        d["uploadedByName"] = uname
                        d["username"] = uname
            elif isinstance(uid, str) and uid in user_map_str:
                addr = user_map_str[uid].get("address") or ""
                d["seller_address"] = addr
                uname = user_map_str[uid].get("username")
                if uname:
                    d["uploadedByName"] = uname
                    d["username"] = uname
            elif isinstance(uid, str) and len(uid) == 24:
                # fallback: try match by _id
                try:
                    oid = ObjectId(uid)
                    u = await users_coll.find_one({"_id": oid}, {"address": 1, "username": 1})
                    if u and u.get("address"):
                        d["seller_address"] = u.get("address")
                    if u and u.get("username"):
                        d["uploadedByName"] = u.get("username")
                        d["username"] = u.get("username")
                except Exception:
                    pass
    except Exception:
        # ignore enrichment failures silently; return basic items
        pass
    
    # Additional debug logging: Show card IDs being returned
    if debug_user_id and items:
        print(f"=== DEBUG: Card IDs being returned ===")
        for item in items[:5]:  # Show first 5 items to avoid spam
            card_id = item.get("id")
            card_name = item.get("card_name", "Unknown")
            print(f"Card: {card_name} | ID: {card_id} | Type: {type(card_id)}")
        print("=" * 40)

    return items


@router.post("/{card_id}/advertise")
async def advertise_card(card_id: str, mdb=Depends(get_mongo_db)):
    """Mark an uploaded card as advertised (idempotent)."""
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Requires MongoDB")
    coll = mdb["uploadedCards"]
    try:
        # Accept either integer id or string id
        query = None
        if str(card_id).isdigit():
            query = {"id": int(card_id)}
        else:
            query = {"id": card_id}
        updated = await coll.find_one_and_update(query, {"$set": {"is_advertised": True}}, return_document=ReturnDocument.AFTER)
        if not updated:
            raise HTTPException(status_code=404, detail="card not found")
        return _normalize_uploaded_card(updated)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
@router.post("")
async def create_uploaded_card(payload: Dict[str, Any], mdb=Depends(get_mongo_db)) -> Dict[str, Any]:
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Requires MongoDB")
    category = (payload.get("category") or "").strip().lower()
    if not category:
        raise HTTPException(status_code=400, detail="category required")

    now = datetime.now(timezone.utc)
    doc: Dict[str, Any] = {
        "category": category,
        "createdAt": now,
        # prefer explicit uploadDate from payload; if not provided, set to now
        "uploadDate": now,
    }

    # Assign auto-incrementing integer id
    doc["id"] = await _next_sequence(mdb, "uploadedCards")

    # If pokemon or yugioh, accept extra fields
    if category in {"pokemon", "yugioh"}:
        # Accept common fields, including variants (and forgiving misspelling varients)
        for key in ["card_name", "rarity", "language", "set", "card_num", "variants"]:
            if key in payload:
                doc[key] = payload[key]
        # handle misspelling 'varients' by mapping to 'variants' if not already set
        if "variants" not in doc and "varients" in payload:
            doc["variants"] = payload.get("varients")

    # Optional price (number). Accept string or number; store as float.
    if "price" in payload and payload["price"] is not None:
        price_raw = payload["price"]
        price_val: float
        try:
            if isinstance(price_raw, (int, float)):
                price_val = float(price_raw)
            elif isinstance(price_raw, str):
                price_val = float(price_raw.replace(",", "").strip())
            else:
                raise ValueError("invalid price type")
        except Exception:
            raise HTTPException(status_code=400, detail="price must be a number")
        if price_val < 0:
            raise HTTPException(status_code=400, detail="price must be >= 0")
        doc["price"] = price_val

    # Optional uploadDate (accept ISO string or epoch millis) -> store datetime
    if "uploadDate" in payload and payload["uploadDate"] is not None:
        ud_raw = payload["uploadDate"]
        try:
            if isinstance(ud_raw, (int, float)):
                doc["uploadDate"] = datetime.fromtimestamp(float(ud_raw) / (1000 if float(ud_raw) > 1e12 else 1), tz=timezone.utc)
            elif isinstance(ud_raw, str):
                # try to parse ISO 8601
                doc["uploadDate"] = datetime.fromisoformat(ud_raw.replace("Z", "+00:00"))
            elif isinstance(ud_raw, datetime):
                doc["uploadDate"] = ud_raw
        except Exception:
            raise HTTPException(status_code=400, detail="uploadDate must be ISO string or epoch time")

    # Optional uploadedBy (user id). Accept either legacy numeric IDs or string userId (e.g., 'usr_...').
    if "uploadedBy" in payload and payload["uploadedBy"] is not None:
        ub_raw = payload["uploadedBy"]
        try:
            if isinstance(ub_raw, str):
                s = ub_raw.strip()
                if s.isdigit():
                    # legacy numeric id, store as Decimal128
                    doc["uploadedBy"] = Decimal128(s)
                else:
                    # string userId (e.g., 'usr_...'), store as-is
                    doc["uploadedBy"] = s
            elif isinstance(ub_raw, (int, float)):
                doc["uploadedBy"] = Decimal128(str(int(ub_raw)))
            else:
                # ignore unknown types
                pass
        except Exception:
            # ignore if cannot parse; leave out uploadedBy
            pass

    # Denormalize seller address and username at write time for easy reads
    try:
        users_coll = mdb["users"]
        ub = doc.get("uploadedBy")
        user_doc = None
        if isinstance(ub, str):
            # try userId match; if looks like ObjectId, fallback to _id
            if len(ub) == 24:
                try:
                    user_doc = await users_coll.find_one({"_id": ObjectId(ub)}, {"address": 1, "username": 1})
                except Exception:
                    user_doc = None
            if not user_doc:
                user_doc = await users_coll.find_one({"userId": ub}, {"address": 1, "username": 1})
        elif isinstance(ub, Decimal128):
            # legacy numeric id path (if users schema had numeric id)
            try:
                user_doc = await users_coll.find_one({"id": ub}, {"address": 1, "username": 1})
            except Exception:
                user_doc = None
        if user_doc and user_doc.get("address"):
            doc["seller_address"] = user_doc.get("address")
        if user_doc and user_doc.get("username"):
            doc["uploadedByName"] = user_doc.get("username")
            doc["username"] = user_doc.get("username")
    except Exception:
        pass

    # Optional image: accept data URL or raw base64 in payload.image_base64
    img_b64 = payload.get("image_base64")
    if isinstance(img_b64, str) and img_b64.strip():
        s = img_b64.strip()
        content_type = None
        # data URL format: data:image/jpeg;base64,XXXX
        m = re.match(r"^data:([\w\-/]+);base64,(.*)$", s)
        if m:
            content_type = m.group(1)
            s = m.group(2)
        try:
            log.debug("create_uploaded_card: image_base64 present (len=%s, content_type=%s)", len(s), content_type)
        except Exception:
            pass
        try:
            # lightweight size guard (~3/4 base64 -> bytes)
            approx_bytes = int(len(s) * 0.75)
            if approx_bytes > 10 * 1024 * 1024:  # 10MB
                raise HTTPException(status_code=400, detail="image too large (max 10MB)")
            raw = base64.b64decode(s, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid image_base64")
        # Prefer local filesystem storage for predictable URLs like /images/local/uploads/<id>.jpg
        prefer_fs = (os.getenv("PREFER_FILESYSTEM_UPLOADS", "true").lower() in ("1", "true", "yes"))
        if prefer_fs:
            try:
                media_root = os.getenv("MEDIA_ROOT") or str(Path(__file__).resolve().parents[2] / "media")
                uploads_dir = Path(media_root) / "uploads"
                uploads_dir.mkdir(parents=True, exist_ok=True)
                # Use a random ObjectId-based filename, default jpg
                ext = ".jpg"
                if content_type == "image/png":
                    ext = ".png"
                fname = f"{ObjectId()}{ext}"
                fpath = uploads_dir / fname
                with open(fpath, "wb") as fh:
                    fh.write(raw)
                doc["image_id"] = fname
                doc["image_url"] = f"/images/local/uploads/{fname}"
                log.info("create_uploaded_card: stored image to filesystem path=%s", str(fpath))
            except Exception as e2:
                try:
                    log.error("filesystem store failed (%s: %s)", e2.__class__.__name__, e2)
                except Exception:
                    pass
                raise HTTPException(status_code=500, detail=f"failed to store image (filesystem): {e2.__class__.__name__}: {e2}")
        else:
            # GridFS path retained as optional alternative when explicitly configured
            try:
                bucket = AsyncIOMotorGridFSBucket(mdb, bucket_name="uploads")
                # Use upload_from_stream for broad Motor compatibility (async and simple)
                try:
                    oid = await bucket.upload_from_stream(
                        filename="card.jpg",
                        source=raw,
                        metadata={"contentType": content_type or "image/jpeg"},
                    )
                except TypeError:
                    # Older Motor/PyMongo versions may not support 'metadata' kw-arg
                    oid = await bucket.upload_from_stream(
                        filename="card.jpg",
                        source=raw,
                    )
                # save references
                doc["image_id"] = str(oid)
                doc["image_url"] = f"/images/{str(oid)}"
                try:
                    log.info("create_uploaded_card: stored image bytes=%s id=%s", len(raw), str(oid))
                except Exception:
                    pass
            except HTTPException:
                raise
            except Exception as e:
                # No fallback since local FS is explicitly disabled
                raise HTTPException(status_code=500, detail=f"failed to store image (gridfs): {e.__class__.__name__}: {e}")

    await mdb["uploadedCards"].insert_one(doc)
    out = doc.copy()
    # Remove MongoDB ObjectId which isn't JSON serializable by Pydantic
    out.pop("_id", None)
    # Normalize problematic types for JSON response
    if "uploadedBy" in out and out["uploadedBy"] is not None:
        ub = out["uploadedBy"]
        try:
            if isinstance(ub, Decimal128):
                out["uploadedBy"] = int(ub.to_decimal())
            elif isinstance(ub, (int, float, str)):
                out["uploadedBy"] = int(str(ub)) if str(ub).isdigit() else str(ub)
        except Exception:
            out["uploadedBy"] = str(ub)
    for k in ("uploadDate", "createdAt"):
        if k in out and isinstance(out[k], datetime):
            out[k] = out[k].astimezone(timezone.utc).isoformat()
    return out

@router.get("/{card_id}")
async def get_uploaded_card(card_id: int, mdb=Depends(get_mongo_db)) -> Dict[str, Any]:
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Requires MongoDB")
    doc = await mdb["uploadedCards"].find_one({"id": int(card_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="not found")
    return _normalize_uploaded_card(doc)
