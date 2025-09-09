from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from bson import ObjectId
from datetime import datetime, timedelta, timezone
import os
import random
import bcrypt
from ..mongo import get_mongo_db, mongo_enabled
from ..schemas.auth import LoginRequest, LoginResponse, UserPublic
from ..services.notify import send_sms, send_email, twilio_enabled, sendgrid_enabled, sms_enabled, solapi_enabled
import logging
from typing import Optional, Set
from pathlib import Path
import base64

# Google ID token verification
try:
    from google.oauth2 import id_token as google_id_token  # type: ignore
    from google.auth.transport import requests as google_requests  # type: ignore
except Exception:  # pragma: no cover
    google_id_token = None  # type: ignore
    google_requests = None  # type: ignore

router = APIRouter()
log = logging.getLogger("uvicorn.error")


def _google_client_ids() -> Set[str]:
    raw = os.getenv("GOOGLE_CLIENT_IDS") or os.getenv("GOOGLE_OAUTH_CLIENT_IDS") or ""
    return {s.strip() for s in raw.split(",") if s.strip()}


def _verify_google_id_token(id_token: str) -> dict:
    if google_id_token is None or google_requests is None:
        raise HTTPException(status_code=503, detail="google-auth not installed on server")
    try:
        req = google_requests.Request()
        info = google_id_token.verify_oauth2_token(id_token, req)
        if info.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("invalid issuer")
        allowed = _google_client_ids()
        aud = info.get("aud")
        if allowed and aud not in allowed:
            raise ValueError("audience not allowed")
        email = info.get("email")
        if not email:
            raise ValueError("email not present in token")
        return info
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"invalid google id_token: {e}")


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Auth requires MongoDB")
    users = mdb["users"]
    doc = await users.find_one({"email": payload.email.lower()})
    if not doc:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    pw_hash = doc.get("password")
    if not pw_hash or not bcrypt.checkpw(payload.password.encode("utf-8"), pw_hash.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # sanitize
    doc_id = str(doc.get("_id")) if doc.get("_id") else None
    doc.pop("_id", None)
    doc.pop("password", None)

    # Convert ObjectIds in favorites -> strings
    favorites_list = []
    for s in (doc.get("favorites") or []):
        try:
            favorites_list.append(str(s) if isinstance(s, ObjectId) else str(s))
        except Exception:
            pass
    doc["favorites"] = favorites_list
    
    # Debug logging for login
    username = doc.get("username", "Unknown")
    print(f"=== DEBUG: User login - {username} ===")
    print(f"User ID: {doc.get('userId')}")
    print(f"Favorites being returned: {favorites_list}")
    print("=" * 50)

    user_public = UserPublic(id=doc_id, **doc)
    return LoginResponse(user=user_public)


# === Simple verification codes (dev-friendly) ===
DEV_MODE = os.getenv("DEV_MODE", "true").lower() in {"1", "true", "yes"}


async def _ensure_verification_indexes(mdb):
    coll = mdb["verifications"]
    # TTL-like: expireAfterSeconds works only on top-level date field
    try:
        await coll.create_index("expiresAt", expireAfterSeconds=0)
    except Exception:
        pass
    try:
        await coll.create_index([("target", 1), ("kind", 1), ("verified", 1)])
    except Exception:
        pass


def _code() -> str:
    return f"{random.randint(0, 999999):06d}"


@router.post("/request-phone-code")
async def request_phone_code(payload: dict, background: BackgroundTasks, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Verification requires MongoDB")
    await _ensure_verification_indexes(mdb)
    cc = str(payload.get("countryCode") or "")
    num = str(payload.get("phone") or "")
    if not cc or not num:
        raise HTTPException(status_code=400, detail="countryCode and phone required")
    target = f"{cc}{num}"
    code = _code()
    expires = datetime.now(timezone.utc) + timedelta(minutes=1)
    doc = {"kind": "phone", "target": target, "code": code, "expiresAt": expires, "verified": False, "createdAt": datetime.now(timezone.utc)}
    res = await mdb["verifications"].insert_one(doc)
    # Send SMS if configured (Twilio or Solapi); otherwise, return devCode in DEV_MODE or error if disabled
    if sms_enabled():
        try:
            provider = "twilio" if twilio_enabled() else ("solapi" if solapi_enabled() else "sms")
            log.info("request_phone_code: sending SMS via %s to target=%s****", provider, target[:-4])
        except Exception:
            pass
        background.add_task(send_sms, target, f"카트 인증코드: {code}")
        return {"verificationId": str(res.inserted_id), "expiresIn": 60}
    if DEV_MODE:
        # No provider configured; return devCode to unblock local testing
        return {"verificationId": str(res.inserted_id), "expiresIn": 60, "devCode": code}
    # In non-dev, fail loudly so the client can surface a proper error
    raise HTTPException(status_code=503, detail="SMS provider not configured")


@router.post("/verify-phone-code")
async def verify_phone_code(payload: dict, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Verification requires MongoDB")
    vid = payload.get("verificationId")
    code = str(payload.get("code") or "")
    if not vid or not code:
        raise HTTPException(status_code=400, detail="verificationId and code required")
    doc = await mdb["verifications"].find_one({"_id": ObjectId(vid)})
    if not doc:
        raise HTTPException(status_code=400, detail="Invalid verificationId")
    # Normalize timezone: Mongo may return naive datetimes; treat naive as UTC
    expires_at = doc.get("expiresAt")
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if isinstance(expires_at, datetime) and expires_at < now:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CODE_EXPIRED",
                "message": "인증 시간이 만료되었습니다. 다시 인증 코드를 요청해 주세요.",
            },
        )
    if doc.get("code") != code:
        raise HTTPException(status_code=400, detail="Invalid code")
    await mdb["verifications"].update_one({"_id": doc["_id"]}, {"$set": {"verified": True}})
    return {"ok": True, "target": doc.get("target")}


@router.post("/request-email-code")
async def request_email_code(payload: dict, background: BackgroundTasks, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Verification requires MongoDB")
    await _ensure_verification_indexes(mdb)
    email = str(payload.get("email") or "").lower()
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    code = _code()
    expires = datetime.now(timezone.utc) + timedelta(minutes=1)
    doc = {"kind": "email", "target": email, "code": code, "expiresAt": expires, "verified": False, "createdAt": datetime.now(timezone.utc)}
    res = await mdb["verifications"].insert_one(doc)
    # Send Email if configured; otherwise, return devCode in DEV_MODE
    if sendgrid_enabled():
        subject = "CardTraders 이메일 인증코드"
        body_text = f"인증코드: {code} (1분 내에 입력)"
        body_html = f"<p>인증코드: <b>{code}</b></p><p>1분 내에 입력해 주세요.</p>"
        background.add_task(send_email, email, subject, body_text, body_html)
    return {"verificationId": str(res.inserted_id), "expiresIn": 60, **({"devCode": code} if not sendgrid_enabled() and DEV_MODE else {})}


@router.post("/verify-email-code")
async def verify_email_code(payload: dict, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Verification requires MongoDB")
    vid = payload.get("verificationId")
    code = str(payload.get("code") or "")
    if not vid or not code:
        raise HTTPException(status_code=400, detail="verificationId and code required")
    doc = await mdb["verifications"].find_one({"_id": ObjectId(vid)})
    if not doc:
        raise HTTPException(status_code=400, detail="Invalid verificationId")
    expires_at = doc.get("expiresAt")
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if isinstance(expires_at, datetime) and expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CODE_EXPIRED",
                "message": "인증 시간이 만료되었습니다. 다시 인증 코드를 요청해 주세요.",
            },
        )
    if doc.get("code") != code:
        raise HTTPException(status_code=400, detail="Invalid code")
    await mdb["verifications"].update_one({"_id": doc["_id"]}, {"$set": {"verified": True}})
    return {"ok": True, "target": doc.get("target")}


@router.post("/signup", response_model=LoginResponse)
async def signup(payload: dict, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Signup requires MongoDB")
    users = mdb["users"]
    email = str(payload.get("email") or "").lower()
    password = str(payload.get("password") or "")
    username = str(payload.get("username") or "")
    countryCode = str(payload.get("countryCode") or "")
    phone = str(payload.get("phone") or "")
    address = str(payload.get("address") or "")
    pfp_url = payload.get("pfp_url")
    emailVid = payload.get("emailVerificationId")
    phoneVid = payload.get("phoneVerificationId")
    if not (email and password and username and countryCode and phone and address and emailVid and phoneVid):
        raise HTTPException(status_code=400, detail="Missing required fields")
    # verify email
    ev = await mdb["verifications"].find_one({"_id": ObjectId(emailVid), "kind": "email", "target": email, "verified": True})
    if not ev:
        raise HTTPException(status_code=400, detail="Email not verified")
    # verify phone
    target_phone = f"{countryCode}{phone}"
    pv = await mdb["verifications"].find_one({"_id": ObjectId(phoneVid), "kind": "phone", "target": target_phone, "verified": True})
    if not pv:
        raise HTTPException(status_code=400, detail="Phone not verified")
    # email unique
    if await users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="Email already exists")
    # hash
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(12)).decode("utf-8")
    doc = {
        "userId": f"usr_{ObjectId()}",
        "username": username,
        "email": email,
        "password": pw_hash,
        "phone_num": f"{countryCode} {phone}",
        "address": address,
        "signup_date": datetime.now(timezone.utc).strftime("%Y/%m/%d"),
        "suggested_num": 0,
        "favorites": [],
        "messages": [],
        "premade_messages": [],
        "notification": True,
        "blocked_users": [],
        "pfp": {"url": pfp_url, "storage": "url" if pfp_url else None},
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }
    res = await users.insert_one(doc)
    doc_id = str(res.inserted_id)
    out = doc.copy()
    out.pop("password", None)
    return {"user": UserPublic(id=doc_id, **out)}


@router.post("/login-google", response_model=LoginResponse)
async def login_google(payload: dict, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Auth requires MongoDB")
    id_token = str(payload.get("idToken") or payload.get("id_token") or "")
    if not id_token:
        raise HTTPException(status_code=400, detail="idToken required")
    info = _verify_google_id_token(id_token)
    email = info.get("email")
    name = info.get("name") or ""
    picture = info.get("picture")
    sub = info.get("sub")
    users = mdb["users"]
    doc = await users.find_one({"email": email})
    if not doc:
        # create minimal account; mark as incomplete until profile finished
        now = datetime.now(timezone.utc)
        doc = {
            "userId": f"usr_{ObjectId()}",
            "username": name or "",
            "email": email,
            "phone_num": None,
            "address": None,
            "signup_date": now.strftime("%Y/%m/%d"),
            "suggested_num": 0,
            "favorites": [],
            "messages": [],
            "premade_messages": [],
            "notification": True,
            "blocked_users": [],
            "pfp": {"url": picture, "storage": "url" if picture else None},
            "createdAt": now,
            "updatedAt": now,
            "auth_provider": "google",
            "google_id": sub,
            "profile_complete": False,
        }
        res = await users.insert_one(doc)
        doc["_id"] = res.inserted_id
    # sanitize
    doc_id = str(doc.get("_id")) if doc.get("_id") else None
    out = doc.copy()
    out.pop("_id", None)
    out.pop("password", None)
    return {"user": UserPublic(id=doc_id, **out)}


@router.post("/update-profile", response_model=LoginResponse)
async def update_profile(payload: dict, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Auth requires MongoDB")
    users = mdb["users"]
    # Identify user by id (Mongo _id) or userId or email
    user_doc = None
    q = None
    id_str = payload.get("id")
    user_id = payload.get("userId")
    email = (payload.get("email") or "").strip().lower() or None
    if id_str:
        try:
            q = {"_id": ObjectId(id_str)}
        except Exception:
            raise HTTPException(status_code=400, detail="invalid id")
    elif user_id:
        q = {"userId": str(user_id)}
    elif email:
        q = {"email": email}
    else:
        raise HTTPException(status_code=400, detail="missing identifier (id, userId, or email)")

    user_doc = await users.find_one(q)
    if not user_doc:
        raise HTTPException(status_code=404, detail="user not found")

    updates: dict = {}
    now = datetime.now(timezone.utc)
    # Simple fields
    if "username" in payload:
        updates["username"] = str(payload.get("username") or "").strip()
    if email is not None:
        # If changing email, ensure uniqueness
        if email != (user_doc.get("email") or "").lower():
            if await users.find_one({"email": email, "_id": {"$ne": user_doc["_id"]}}):
                raise HTTPException(status_code=409, detail="Email already exists")
            updates["email"] = email
    if "phone_num" in payload:
        updates["phone_num"] = str(payload.get("phone_num") or "").strip()
    if "address" in payload:
        updates["address"] = str(payload.get("address") or "").strip()
    # Favorites
    if "favorites" in payload:
        v = payload.get("favorites")
        if v is None:
            updates["favorites"] = []
        elif isinstance(v, list):
            cleaned = []
            for item in v:
                try:
                    cleaned.append(str(item))
                except Exception:
                    pass
            updates["favorites"] = cleaned
        else:
            try:
                updates["favorites"] = [str(v)]
            except Exception:
                updates["favorites"] = []
    # Bank account (optional)
    if "bank_acc" in payload:
        # Allow clearing by sending null/empty
        v = payload.get("bank_acc")
        if v is None:
            updates["bank_acc"] = None
        else:
            updates["bank_acc"] = str(v).strip()

    # Avatar processing: accept either direct URL or image_base64; image_base64 takes precedence if present
    image_b64 = payload.get("image_base64")
    pfp_url = payload.get("pfp_url")
    if isinstance(image_b64, str) and image_b64.strip():
        s = image_b64.strip()
        # Allow data URL or raw base64
        try:
            if s.startswith("data:"):
                header, data = s.split(",", 1)
                if ";base64" not in header:
                    raise ValueError("not base64 data url")
                raw = base64.b64decode(data)
                content_type = "image/jpeg"
                if "image/png" in header:
                    content_type = "image/png"
            else:
                raw = base64.b64decode(s)
                content_type = "image/jpeg"
        except Exception:
            raise HTTPException(status_code=400, detail="invalid image_base64")
        # Save under media/uploads similar to chat attachments
        media_root = os.getenv("MEDIA_ROOT") or str(Path(__file__).resolve().parents[2] / "media")
        uploads_dir = Path(media_root) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        # Pick extension
        ext = ".jpg" if content_type == "image/jpeg" else ".png"
        fname = f"avatar_{ObjectId()}{ext}"
        fpath = uploads_dir / fname
        with open(fpath, "wb") as f:
            f.write(raw)
        updates["pfp"] = {"url": f"/images/local/uploads/{fname}", "storage": "local"}
    elif pfp_url is not None:
        # Explicitly set from provided URL or clear when null
        if pfp_url:
            updates["pfp"] = {"url": pfp_url, "storage": "url"}
        else:
            updates["pfp"] = {"url": None, "storage": None}

    if not updates:
        # Nothing to change
        # Return current doc as UserPublic
        # Convert ObjectIds in starred_item -> strings for safety
        starred = []
        for s in (user_doc.get("starred_item") or []):
            try:
                starred.append(str(s) if isinstance(s, ObjectId) else str(s))
            except Exception:
                pass
        user_doc["starred_item"] = starred
        doc_id = str(user_doc.get("_id")) if user_doc.get("_id") else None
        out = user_doc.copy()
        out.pop("_id", None)
        out.pop("password", None)
        return {"user": UserPublic(id=doc_id, **out)}

    updates["updatedAt"] = now
    await users.update_one({"_id": user_doc["_id"]}, {"$set": updates})
    user_doc.update(updates)
    # Sanitize
    doc_id = str(user_doc.get("_id")) if user_doc.get("_id") else None
    out = user_doc.copy()
    out.pop("_id", None)
    out.pop("password", None)
    return {"user": UserPublic(id=doc_id, **out)}


@router.post("/complete-profile-google", response_model=LoginResponse)
async def complete_profile_google(payload: dict, mdb=Depends(get_mongo_db)):
    if not mongo_enabled() or mdb is None:
        raise HTTPException(status_code=503, detail="Auth requires MongoDB")
    id_token = str(payload.get("idToken") or payload.get("id_token") or "")
    username = str(payload.get("username") or "").strip()
    address = str(payload.get("address") or "").strip()
    if not id_token or not username or not address:
        raise HTTPException(status_code=400, detail="idToken, username, address required")
    info = _verify_google_id_token(id_token)
    email = info.get("email")
    users = mdb["users"]
    doc = await users.find_one({"email": email})
    if not doc:
        raise HTTPException(status_code=404, detail="user not found")
    now = datetime.now(timezone.utc)
    updates = {"username": username, "address": address, "updatedAt": now, "profile_complete": True}
    if not doc.get("signup_date"):
        updates["signup_date"] = now.strftime("%Y/%m/%d")
    await users.update_one({"_id": doc["_id"]}, {"$set": updates})
    doc.update(updates)
    doc_id = str(doc.get("_id")) if doc.get("_id") else None
    out = doc.copy()
    out.pop("_id", None)
    out.pop("password", None)
    return {"user": UserPublic(id=doc_id, **out)}
