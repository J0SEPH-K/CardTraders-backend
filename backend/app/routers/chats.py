from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from bson import ObjectId

from ..mongo import get_mongo_db, mongo_enabled
import os
from pathlib import Path
import base64


router = APIRouter()


class ConversationCreateRequest(BaseModel):
    participants: List[str] = Field(..., description="userIds of both participants")
    listingId: Optional[str] = Field(default=None, description="Card/listing id this chat is about")


class SendMessageRequest(BaseModel):
    senderId: str
    text: Optional[str] = None
    imageUrl: Optional[str] = None


def _oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid id")


async def ensure_indexes(mdb):
    conv = mdb["conversations"]
    msg = mdb["messages"]
    # Unique conversation: participantsHash + listingId
    await conv.create_index([("participantsHash", 1), ("listingId", 1)], unique=True, name="unique_convo")
    await conv.create_index([("updatedAt", -1)], name="updated_desc")
    await msg.create_index([("convoId", 1), ("_id", -1)], name="convo_cursor_desc")


# --- WebSocket connection manager for live chat ---
class ChatWSManager:
    def __init__(self) -> None:
        # convoId -> set of websockets
        self.active: Dict[str, set[WebSocket]] = {}

    async def connect(self, convo_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self.active.setdefault(convo_id, set()).add(ws)

    def disconnect(self, convo_id: str, ws: WebSocket) -> None:
        try:
            conns = self.active.get(convo_id)
            if conns and ws in conns:
                conns.remove(ws)
            if conns is not None and len(conns) == 0:
                self.active.pop(convo_id, None)
        except Exception:
            pass

    async def broadcast(self, convo_id: str, data: Dict[str, Any]) -> None:
        conns = list(self.active.get(convo_id, set()))
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                # Drop broken connections
                self.disconnect(convo_id, ws)


ws_manager = ChatWSManager()


@router.post("/conversations/get-or-create")
async def get_or_create_conversation(payload: ConversationCreateRequest, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="mongodb not configured")
    if not payload.participants or len(payload.participants) != 2:
        raise HTTPException(status_code=400, detail="participants must be 2 userIds")
    parts = sorted([str(x) for x in payload.participants])
    participants_hash = "|".join(parts)
    now = datetime.now(timezone.utc)
    conv = await mdb["conversations"].find_one_and_update(
        {"participantsHash": participants_hash, "listingId": payload.listingId or None},
        {
            "$setOnInsert": {
                "participants": parts,
                "participantsHash": participants_hash,
                "listingId": payload.listingId or None,
                "createdAt": now,
                "unread": {parts[0]: 0, parts[1]: 0},
            },
            "$set": {"updatedAt": now},
        },
        upsert=True,
        return_document=True,
    )
    if conv is None:
        # Rare race; fetch again
        conv = await mdb["conversations"].find_one({"participantsHash": participants_hash, "listingId": payload.listingId or None})
    conv["id"] = str(conv.pop("_id"))
    for k in ("createdAt", "updatedAt"):
        if isinstance(conv.get(k), datetime):
            dt = conv[k]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            conv[k] = dt.isoformat()
    lm = conv.get("lastMessage")
    if isinstance(lm, dict) and isinstance(lm.get("at"), datetime):
        dt = lm["at"]
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        lm["at"] = dt.isoformat()
    return conv


@router.get("/conversations")
async def list_conversations(userId: str = Query(...), limit: int = Query(20, ge=1, le=100), cursor: Optional[str] = None, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="mongodb not configured")
    q: Dict[str, Any] = {"participants": userId}
    if cursor:
    # cursor is updatedAt ISO; keep it as date string for simplicity
        try:
            cur_date = datetime.fromisoformat(cursor)
            q["updatedAt"] = {"$lt": cur_date}
        except Exception:
            pass
    docs = mdb["conversations"].find(q).sort("updatedAt", -1).limit(limit)
    res: List[Dict[str, Any]] = []
    async for d in docs:
        d["id"] = str(d.pop("_id"))
        # Normalize datetimes
        for k in ("createdAt", "updatedAt"):
            if isinstance(d.get(k), datetime):
                dt = d[k]
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                d[k] = dt.isoformat()
        lm = d.get("lastMessage")
        if isinstance(lm, dict) and isinstance(lm.get("at"), datetime):
            dt = lm["at"]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            lm["at"] = dt.isoformat()
        res.append(d)
    return {"items": res}


@router.get("/{convoId}/messages")
async def list_messages(convoId: str, beforeId: Optional[str] = None, limit: int = Query(50, ge=1, le=200), mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="mongodb not configured")
    q: Dict[str, Any] = {"convoId": _oid(convoId)}
    if beforeId:
        q["_id"] = {"$lt": _oid(beforeId)}
    cursor = mdb["messages"].find(q).sort("_id", -1).limit(limit)
    items: List[Dict[str, Any]] = []
    async for d in cursor:
        d["id"] = str(d.pop("_id"))
        # Normalize ObjectId and datetime fields for JSON
        if isinstance(d.get("convoId"), ObjectId):
            d["convoId"] = str(d["convoId"])
        at = d.get("at")
        if isinstance(at, datetime):
            dt = at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            d["at"] = dt.isoformat()
        items.append(d)
    # Return chronological asc for UI convenience
    items.reverse()
    return {"items": items}


@router.post("/{convoId}/messages")
async def send_message(convoId: str, payload: SendMessageRequest, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="mongodb not configured")
    conv = await mdb["conversations"].find_one({"_id": _oid(convoId)})
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    if payload.senderId not in conv.get("participants", []):
        raise HTTPException(status_code=403, detail="sender not in conversation")
    now = datetime.now(timezone.utc)
    doc = {
        "convoId": conv["_id"],
        "senderId": payload.senderId,
        "text": payload.text,
        "imageUrl": payload.imageUrl,
        "at": now,
        "status": "sent",
        "readBy": [payload.senderId],
    }
    ins = await mdb["messages"].insert_one(doc)
    # Update conversation lastMessage, updatedAt, and unread counts
    unread = conv.get("unread", {p: 0 for p in conv.get("participants", [])})
    for p in conv.get("participants", []):
        if p == payload.senderId:
            continue
        unread[p] = int(unread.get(p, 0)) + 1
    await mdb["conversations"].update_one(
        {"_id": conv["_id"]},
        {
            "$set": {
                "lastMessage": {"text": payload.text or "", "senderId": payload.senderId, "at": now},
                "updatedAt": now,
                "unread": unread,
            }
        },
    )
    new_id = str(ins.inserted_id)
    # Broadcast new message to websocket clients
    try:
        await ws_manager.broadcast(convoId, {
            "type": "new_message",
            "convoId": convoId,
            "message": {
                "id": new_id,
                "convoId": convoId,
                "senderId": payload.senderId,
                "text": payload.text or None,
                "imageUrl": payload.imageUrl or None,
                "at": now.isoformat(),
                "status": "sent",
            }
        })
    except Exception:
        pass
    return {"id": new_id}


class MarkReadRequest(BaseModel):
    readerId: str


@router.post("/{convoId}/read")
async def mark_read(convoId: str, payload: MarkReadRequest, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="mongodb not configured")
    res = await mdb["conversations"].update_one(
        {"_id": _oid(convoId)},
        {"$set": {f"unread.{payload.readerId}": 0}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="conversation not found")
    try:
        await ws_manager.broadcast(convoId, {"type": "read", "convoId": convoId, "readerId": payload.readerId})
    except Exception:
        pass
    return {"ok": True}


class UploadImageRequest(BaseModel):
    senderId: str
    image_base64: str


@router.post("/{convoId}/attachments")
async def upload_image(convoId: str, payload: UploadImageRequest, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="mongodb not configured")
    conv = await mdb["conversations"].find_one({"_id": _oid(convoId)})
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    if payload.senderId not in conv.get("participants", []):
        raise HTTPException(status_code=403, detail="sender not in conversation")

    s = (payload.image_base64 or "").strip()
    if not s:
        raise HTTPException(status_code=400, detail="image_base64 required")
    content_type = None
    # Accept data URL or raw base64
    if s.startswith("data:"):
        try:
            header, b64 = s.split(",", 1)
            content_type = header.split(";")[0].replace("data:", "")
            s = b64
        except Exception:
            pass
    try:
        approx_bytes = int(len(s) * 0.75)
        if approx_bytes > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="image too large (max 10MB)")
        raw = base64.b64decode(s, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid image_base64")

    # Save to filesystem (reusing images router local serving)
    media_root = os.getenv("MEDIA_ROOT") or str(Path(__file__).resolve().parents[2] / "media")
    uploads_dir = Path(media_root) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    ext = ".jpg"
    if content_type == "image/png":
        ext = ".png"
    fname = f"{ObjectId()}{ext}"
    fpath = uploads_dir / fname
    try:
        with open(fpath, "wb") as fh:
            fh.write(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to store image: {e}")

    image_url = f"/images/local/uploads/{fname}"

    # Create message with imageUrl
    now = datetime.now(timezone.utc)
    doc = {
        "convoId": conv["_id"],
        "senderId": payload.senderId,
        "text": None,
        "imageUrl": image_url,
        "at": now,
        "status": "sent",
        "readBy": [payload.senderId],
    }
    ins = await mdb["messages"].insert_one(doc)

    # Update conversation lastMessage, updatedAt, and unread counts
    unread = conv.get("unread", {p: 0 for p in conv.get("participants", [])})
    for p in conv.get("participants", []):
        if p == payload.senderId:
            continue
        unread[p] = int(unread.get(p, 0)) + 1
    await mdb["conversations"].update_one(
        {"_id": conv["_id"]},
        {
            "$set": {
                "lastMessage": {"text": "", "senderId": payload.senderId, "at": now},
                "updatedAt": now,
                "unread": unread,
            }
        },
    )
    new_id = str(ins.inserted_id)
    # Broadcast new image message to websocket clients
    try:
        await ws_manager.broadcast(convoId, {
            "type": "new_message",
            "convoId": convoId,
            "message": {
                "id": new_id,
                "convoId": convoId,
                "senderId": payload.senderId,
                "text": None,
                "imageUrl": image_url,
                "at": now.isoformat(),
                "status": "sent",
            }
        })
    except Exception:
        pass
    return {"id": new_id, "imageUrl": image_url}


@router.websocket("/ws/{convoId}")
async def chat_ws(convoId: str, websocket: WebSocket):
    # Accept optional userId in query for typing events
    user_id = websocket.query_params.get("userId")
    await ws_manager.connect(convoId, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            # Expect { type: 'typing'|'stop_typing', userId: str }
            if isinstance(data, dict):
                t = data.get("type")
                uid = data.get("userId") or user_id
                if t in ("typing", "stop_typing") and uid:
                    await ws_manager.broadcast(convoId, {"type": "typing", "convoId": convoId, "userId": uid, "isTyping": t == "typing"})
    except WebSocketDisconnect:
        pass
    except Exception:
        # Best-effort close on any failure
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        ws_manager.disconnect(convoId, websocket)
