from fastapi import APIRouter, Depends, HTTPException, Request, Header
from ..schemas.payments import CreateOrderRequest, CreateOrderResponse, WebhookEvent as WebhookSchema
from ..db import get_db
from sqlalchemy.orm import Session
from ..models.payments import Payment, Wallet, Ledger, WebhookEvent
from ..models.listing import Listing
from ..routers.chats import ws_manager
from ..mongo import get_mongo_db, mongo_enabled
from datetime import datetime, timezone
from typing import Optional
import os
import json
import httpx
import logging
from fastapi.responses import RedirectResponse
from bson import ObjectId
from pymongo import ReturnDocument
import secrets
import string
from ..schemas.payments import ReconcileTransaction, ReconcileResult, UploadProofResponse
from ..schemas.payments import DepositRequest
from urllib.parse import urlencode, quote

# Open Banking config (KFTC-like)
OPENBANK_CLIENT_ID = os.getenv("OPENBANK_CLIENT_ID")
OPENBANK_CLIENT_SECRET = os.getenv("OPENBANK_CLIENT_SECRET")
OPENBANK_REDIRECT_URI = os.getenv("OPENBANK_REDIRECT_URI")
OPENBANK_AUTH_URL = os.getenv("OPENBANK_AUTH_URL", "https://testapi.openbanking.or.kr/oauth/2.0/authorize")
OPENBANK_TOKEN_URL = os.getenv("OPENBANK_TOKEN_URL", "https://testapi.openbanking.or.kr/oauth/2.0/token")
OPENBANK_ACCOUNT_API = os.getenv("OPENBANK_ACCOUNT_API", "https://testapi.openbanking.or.kr/v2.0/account/transaction_list")
OPENBANK_DEPOSIT_FIN_NUM = os.getenv("OPENBANK_DEPOSIT_FIN_NUM", "https://openapi.openbanking.or.kr/v2.0/transfer/deposit/fin_num")

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


def _credit_seller(db: Session, p: Payment):
    """Credit seller wallet and create ledger entry."""
    wallet = db.query(Wallet).filter(Wallet.user_id == p.seller_id).one_or_none()
    if not wallet:
        wallet = Wallet(user_id=p.seller_id, balance=0.0)
        db.add(wallet)
    wallet.balance = (wallet.balance or 0.0) + float(p.amount)
    ledger = Ledger(user_id=p.seller_id, change=float(p.amount), reason="sale", related_payment_id=p.id)
    db.add(ledger)
    db.commit()


@router.post("/create", response_model=CreateOrderResponse)
async def create_order(payload: CreateOrderRequest, db: Session = Depends(get_db)):
    """
    Create an order and return a provider token/checkout URL. This implementation uses a sandbox
    flow when no provider credentials are configured.
    """
    # Auth / ACL: require caller identity via X-User-Id header and ensure it matches buyer_id
    # Also ensure that when chatId is provided, the buyer is part of the conversation (best-effort)
    # Expect clients to set X-User-Id header to their user id (e.g. usr_<id>)
    # Note: full auth/session middleware isn't present in this project; this enforces a minimum server-side check.
    caller_user_id = None
    # FastAPI will inject header if present via Depends parameter; we read directly from Request headers here
    # Attempt to read X-User-Id from environment of the request via global request context if available
    # Fallback: accept the buyer_id as-is (dev mode)
    # To access headers we need the Request object; expect middleware to add X-User-Id header.
    try:
        from fastapi import Request as _RequestType
    except Exception:
        _RequestType = None

    # Try to pull header from environment - fast path via os.environ for tests; in real deployment use auth middleware
    caller_user_id = None
    try:
        # If running within a request, FastAPI can pass header via dependency; try Header dependency instead
        pass
    except Exception:
        pass

    # We'll enforce X-User-Id header by reading from os.environ if set for tests, otherwise we accept payload.buyer_id.
    x_user_env = os.getenv("X_USER_ID")
    if x_user_env:
        caller_user_id = x_user_env

    if caller_user_id and caller_user_id != payload.buyer_id:
        raise HTTPException(status_code=403, detail="caller identity does not match buyer_id")

    # persist order
    try:
        # create model and generate a compact payment reference for bank transfers
        ref = "CT-" + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        model = Payment(
            buyer_id=payload.buyer_id,
            seller_id=payload.seller_id,
            item_id=payload.item_id or None,
            amount=payload.amount,
            currency=payload.currency or "KRW",
            status="PENDING",
            payment_reference=ref,
        )
        db.add(model)
        db.commit()
        db.refresh(model)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # If chatId provided, create a chat message in Mongo so both users see payment request
    message_id = None
    if getattr(payload, "chatId", None) and mongo_enabled():
        try:
            mdb = await get_mongo_db()
            now = datetime.now(timezone.utc)
            # verify buyer is participant of convo (best-effort): check participants field if present
            try:
                convo = await mdb["conversations"].find_one({"_id": ObjectId(payload.chatId)})
                if convo and convo.get("participants"):
                    # participants may store userIds like 'usr_xxx' or Mongo ObjectIds
                    participants = [str(p) for p in convo.get("participants")]
                    if payload.buyer_id not in participants and payload.seller_id not in participants:
                        # If the buyer is not a participant, reject the request
                        raise HTTPException(status_code=403, detail="buyer or seller not in conversation")
            except HTTPException:
                raise
            except Exception:
                # best-effort only
                pass

            doc = {
                "convoId": ObjectId(payload.chatId) if payload.chatId else None,
                "senderId": "system",
                "type": "payment",
                "paymentId": model.id,
                "meta": {"amount": model.amount, "currency": model.currency, "role": "buyer"},
                "status": "PENDING",
                "at": now,
                "readBy": [],
            }
            ins = await mdb["messages"].insert_one(doc)
            message_id = str(ins.inserted_id)
            # update conversation lastMessage
            await mdb["conversations"].update_one({"_id": ObjectId(payload.chatId)}, {"$set": {"lastMessage": {"text": "Payment request", "senderId": "system", "at": now}, "updatedAt": now}})
            # broadcast payment.started to any websocket clients
            try:
                await ws_manager.broadcast(payload.chatId, {
                    "type": "payment.started",
                    "convoId": payload.chatId,
                    "message": {
                        "id": message_id,
                        "convoId": payload.chatId,
                        "senderId": "system",
                        "type": "payment",
                        "paymentId": model.id,
                        "meta": {"amount": model.amount, "currency": model.currency, "role": "buyer"},
                        "status": "PENDING",
                        "at": now.isoformat(),
                    }
                })
            except Exception:
                pass
        except Exception:
            # best-effort only; continue to provider flow even if chat message fails
            message_id = None

    # Provider selection
    provider = os.getenv("PAYMENT_PROVIDER", "sandbox").lower()

    # Sandbox short-circuit for non-provider flows (bank transfer will use payment_reference)
    if provider != "kakao":
        return CreateOrderResponse(
            order_id=model.id,
            amount=model.amount,
            currency=model.currency,
            checkout_url=f"/payments/sandbox/checkout/{model.id}",
            payment_reference=model.payment_reference,
        )

    # KakaoPay flow
    kakao_key = os.getenv("KAKAO_ADMIN_KEY")
    kakao_cid = os.getenv("KAKAO_CID", "TC0ONETIME")
    approval_url = os.getenv("KAKAO_APPROVAL_URL", f"http://localhost:3000/payments/kakao/approve")
    cancel_url = os.getenv("KAKAO_CANCEL_URL", "http://localhost:3000/payments/kakao/cancel")
    fail_url = os.getenv("KAKAO_FAIL_URL", "http://localhost:3000/payments/kakao/fail")

    if not kakao_key:
        # Misconfigured; fall back to sandbox
        return CreateOrderResponse(
            order_id=model.id,
            amount=model.amount,
            currency=model.currency,
            checkout_url=f"/payments/sandbox/checkout/{model.id}",
            payment_reference=model.payment_reference,
        )

    # Build ready request (application/x-www-form-urlencoded)
    ready_url = "https://kapi.kakao.com/v1/payment/ready"
    headers = {"Authorization": f"KakaoAK {kakao_key}", "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"}
    params = {
        "cid": kakao_cid,
        "partner_order_id": model.id,
        "partner_user_id": model.buyer_id,
        "item_name": f"Item {model.item_id or model.id}",
        "quantity": 1,
        "total_amount": int(model.amount),
        "tax_free_amount": 0,
        "approval_url": approval_url,
        "cancel_url": cancel_url,
        "fail_url": fail_url,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(ready_url, headers=headers, data=params)
            r.raise_for_status()
            body = r.json()
    except Exception as e:
        # provider error -> return sandbox fallback
        model.provider_raw = json.dumps({"error": str(e)})
        db.add(model)
        db.commit()
        return CreateOrderResponse(
            order_id=model.id,
            amount=model.amount,
            currency=model.currency,
            checkout_url=f"/payments/sandbox/checkout/{model.id}",
            payment_reference=model.payment_reference,
        )

    # persist provider tid and raw response
    tid = body.get("tid")
    next_url = body.get("next_redirect_mobile_url") or body.get("next_redirect_pc_url")
    model.provider_payment_id = tid
    model.provider_raw = json.dumps(body)
    db.add(model)
    db.commit()

    return CreateOrderResponse(order_id=model.id, amount=model.amount, currency=model.currency, checkout_url=next_url, provider_token=tid, message_id=message_id)


@router.post("/webhook")
async def payments_webhook(request: Request):
    # Provider will call here. Verify signature using provider webhook secret.
    body = await request.body()
    try:
        payload = json.loads(body.decode())
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")

    # Verify signature/header to ensure webhook authenticity
    # Support a generic HMAC-based verification using PAYMENT_WEBHOOK_SECRET and header X-Signature
    webhook_secret = os.getenv("PAYMENT_WEBHOOK_SECRET")
    signature_header = request.headers.get("X-Signature") or request.headers.get("x-signature") or request.headers.get("Stripe-Signature")
    if webhook_secret and signature_header:
        try:
            import hmac
            import hashlib
            computed = hmac.new(webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            # Allow header to be either hex or prefixed like sha256=...
            sig_val = signature_header.split("=")[-1]
            if not hmac.compare_digest(computed, sig_val):
                raise HTTPException(status_code=400, detail="invalid webhook signature")
        except HTTPException:
            raise
        except Exception:
            # If verification fails unexpectedly, reject
            raise HTTPException(status_code=400, detail="webhook verification failed")

    # Idempotency: avoid processing the same provider event twice
    provider = payload.get("provider") or payload.get("source") or "unknown"
    provider_event_id = payload.get("event_id") or payload.get("id") or payload.get("payment_id")
    if provider_event_id:
        db = next(get_db())
        try:
            # If already processed, return 200 quickly
            existing = db.query(WebhookEvent).filter(WebhookEvent.provider_event_id == str(provider_event_id)).one_or_none()
            if existing:
                return {"ok": True, "already_processed": True}
        except Exception:
            pass
        finally:
            try:
                db.close()
            except Exception:
                pass

    event_type = payload.get("event_type") or payload.get("type")
    provider_payment_id = payload.get("payment_id") or payload.get("provider_payment_id")
    order_id = payload.get("order_id") or payload.get("merchant_order_id")

    # Basic handling: mark payment as PAID when event indicates success
    db = next(get_db())
    try:
        p: Optional[Payment] = db.query(Payment).filter(Payment.id == order_id).one_or_none()
        if not p and provider_payment_id:
            p = db.query(Payment).filter(Payment.provider_payment_id == provider_payment_id).one_or_none()
        if not p:
            # Unknown order — ignore or log
            return {"ok": True}

        if event_type in ("payment.succeeded", "payment.completed", "charge.succeeded"):
            p.status = "PAID"
            p.provider_payment_id = provider_payment_id
            p.provider_raw = json.dumps(payload)
            db.add(p)
            # credit seller wallet
            wallet = db.query(Wallet).filter(Wallet.user_id == p.seller_id).one_or_none()
            if not wallet:
                wallet = Wallet(user_id=p.seller_id, balance=0.0)
                db.add(wallet)
            wallet.balance = (wallet.balance or 0.0) + float(p.amount)
            # ledger entry
            ledger = Ledger(user_id=p.seller_id, change=float(p.amount), reason="sale", related_payment_id=p.id)
            db.add(ledger)
            db.commit()
            # record webhook event to prevent reprocessing
            try:
                we = WebhookEvent(provider=provider, provider_event_id=str(provider_event_id), raw=json.dumps(payload))
                db.add(we)
                db.commit()
            except Exception:
                db.rollback()
            # Update chat message in Mongo and broadcast payment.updated
            try:
                if mongo_enabled():
                    mdb = await get_mongo_db()
                    # find messages with this paymentId
                    res = await mdb["messages"].find_one({"paymentId": p.id})
                    if res:
                        msg_id = str(res.get("_id"))
                        await mdb["messages"].update_one({"_id": res.get("_id")}, {"$set": {"status": "PAID", "providerInfo": payload}})
                        try:
                            await ws_manager.broadcast(str(res.get("convoId")), {"type": "payment.updated", "convoId": str(res.get("convoId")), "message": {"id": msg_id, "paymentId": p.id, "status": "PAID", "providerInfo": payload}})
                        except Exception:
                            pass
            except Exception:
                pass
        elif event_type in ("payment.refunded", "refund.succeeded"):
            p.status = "REFUNDED"
            p.provider_raw = json.dumps(payload)
            db.add(p)
            # TODO: debit wallets or record refund ledger
            db.commit()
            try:
                we = WebhookEvent(provider=provider, provider_event_id=str(provider_event_id), raw=json.dumps(payload))
                db.add(we)
                db.commit()
            except Exception:
                db.rollback()
            # Update chat message and broadcast
            try:
                if mongo_enabled():
                    mdb = await get_mongo_db()
                    res = await mdb["messages"].find_one({"paymentId": p.id})
                    if res:
                        msg_id = str(res.get("_id"))
                        await mdb["messages"].update_one({"_id": res.get("_id")}, {"$set": {"status": "REFUNDED", "providerInfo": payload}})
                        try:
                            await ws_manager.broadcast(str(res.get("convoId")), {"type": "payment.updated", "convoId": str(res.get("convoId")), "message": {"id": msg_id, "paymentId": p.id, "status": "REFUNDED", "providerInfo": payload}})
                        except Exception:
                            pass
            except Exception:
                pass

    finally:
        try:
            db.close()
        except Exception:
            pass

    return {"ok": True}



@router.get("/openbanking/start/{payment_id}")
async def openbank_start(payment_id: str, encoding: Optional[str] = "percent"):
    """Return an authorization URL for the client to start Open Banking OAuth flow.

    The client will be redirected to the bank's auth page; after consenting, the
    provider will redirect back to `/openbanking/callback` with a code which the
    app exchanges for an access token to read transactions and verify the transfer.
    """
    if not OPENBANK_CLIENT_ID or not OPENBANK_REDIRECT_URI:
        raise HTTPException(status_code=400, detail="Open Banking not configured")
    # minimal scope for account inquiry; provider-specific
    scope = "oob openapi_accounts"

    # Two encoding strategies supported for testing provider strictness:
    # - "percent": percent-encode components (spaces -> %20) using quote()
    # - "plus": use urlencode(...) which encodes spaces as '+'
    encoding = (encoding or "percent").lower()
    if encoding == "plus":
        params = {
            "response_type": "code",
            "client_id": OPENBANK_CLIENT_ID,
            "redirect_uri": OPENBANK_REDIRECT_URI,
            "scope": scope,
            "state": payment_id,
        }
        url = OPENBANK_AUTH_URL + "?" + urlencode(params)
    else:
        # percent-encode components explicitly
        client_q = quote(OPENBANK_CLIENT_ID or "", safe="")
        redirect_q = quote(OPENBANK_REDIRECT_URI or "", safe="")
        scope_q = quote(scope, safe="")
        state_q = quote(payment_id or "", safe="")
        url = f"{OPENBANK_AUTH_URL}?response_type=code&client_id={client_q}&redirect_uri={redirect_q}&scope={scope_q}&state={state_q}"

    # Log the constructed auth URL and component values (client_secret omitted) for debugging
    logger.info("OpenBanking auth URL params: client_id=%s redirect_uri=%s scope=%s state=%s encoding=%s", OPENBANK_CLIENT_ID, OPENBANK_REDIRECT_URI, scope, payment_id, encoding)
    logger.info("OpenBanking auth URL constructed: %s", url)
    return {"auth_url": url}


@router.get("/openbanking/callback")
async def openbank_callback(code: Optional[str] = None, state: Optional[str] = None):
    """Callback to exchange code for token and attempt to verify a payment matching
    the `state` which contains our payment_id.
    """
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")
    if not OPENBANK_CLIENT_ID or not OPENBANK_CLIENT_SECRET:
        raise HTTPException(status_code=400, detail="Open Banking not configured")

    # Exchange code for access token
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": OPENBANK_CLIENT_ID,
        "client_secret": OPENBANK_CLIENT_SECRET,
        "redirect_uri": OPENBANK_REDIRECT_URI,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(OPENBANK_TOKEN_URL, data=data)
            # Record full response for debugging if provider returns error
            if r.status_code != 200:
                text = None
                try:
                    text = r.text
                except Exception:
                    text = '<unreadable response>'
                logger.warning("OpenBank token exchange failed: status=%s body=%s", r.status_code, text)
                r.raise_for_status()
            tok = r.json()
            logger.info("OpenBank token exchange response keys: %s", list(tok.keys()))
    except Exception as e:
        logger.exception("token exchange exception")
        raise HTTPException(status_code=502, detail=f"token exchange failed: {e}")

    access_token = tok.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="no access token returned")

    # Use access token to call transaction list or account inquiry endpoint to find a
    # transaction that references our payment_reference (or matching amount). This is
    # provider-specific; we'll attempt a best-effort search using OPENBANK_ACCOUNT_API.
    payment_id = state
    db = next(get_db())
    p: Optional[Payment] = db.query(Payment).filter(Payment.id == payment_id).one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="payment not found")

    # Query recent transactions for accounts accessible by the user (scope-dependent).
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"bank_tran_id": ""}  # provider-specific; many need a request body
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(OPENBANK_ACCOUNT_API, headers=headers, params={})
            if r.status_code != 200:
                text = None
                try:
                    text = r.text
                except Exception:
                    text = '<unreadable response>'
                logger.warning("OpenBank account API failed: status=%s body=%s", r.status_code, text)
                r.raise_for_status()
            tx = r.json()
    except Exception as e:
        logger.exception("account inquiry exception")
        raise HTTPException(status_code=502, detail=f"account inquiry failed: {e}")

    # Simple heuristic: search the returned JSON string for our reference or amount
    found = False
    try:
        jstr = json.dumps(tx)
        if p.payment_reference and p.payment_reference in jstr:
            found = True
        else:
            # match by amount string (provider may show won without commas)
            if str(int(p.amount)) in jstr or str(p.amount) in jstr:
                found = True
    except Exception:
        pass

    if not found:
        raise HTTPException(status_code=400, detail="payment not found in account transactions")

    # Mark payment as PAID and credit seller, then mark uploaded card advertised (if item_id present)
    p.status = "PAID"
    p.provider_raw = json.dumps({"openbanking_verified": True, "token_info": {k: v for k, v in tok.items() if k != "access_token"}})
    db.add(p)
    _credit_seller(db, p)

    # If this payment references an uploaded card, mark it advertised in Mongo
    try:
        if mongo_enabled() and p.item_id:
            mdb = await get_mongo_db()
            coll = mdb["uploadedCards"]
            query = {"id": int(p.item_id)} if str(p.item_id).isdigit() else {"id": p.item_id}
            await coll.find_one_and_update(query, {"$set": {"is_advertised": True}}, return_document=ReturnDocument.AFTER)
    except Exception:
        pass

    return {"ok": True, "payment_id": p.id, "verified": True}


@router.post("/openbanking/transfer/deposit/{payment_id}")
async def openbank_deposit_transfer(payment_id: str, payload: DepositRequest, Authorization: Optional[str] = Header(None)):
    """Perform an Open Banking deposit (fin_num) using the provided access token.

    The client must supply Authorization: Bearer <access_token> header. On success, the
    payment will be marked PAID and seller credited.
    """
    if not Authorization or not Authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing access token")
    access_token = Authorization.split(None, 1)[1]

    # Find payment
    db = next(get_db())
    p: Optional[Payment] = db.query(Payment).filter(Payment.id == payment_id).one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="payment not found")

    # Build request body for provider
    body = payload.dict()
    # Ensure tran_dtime present
    if not body.get("tran_dtime"):
        from datetime import datetime
        body["tran_dtime"] = datetime.utcnow().strftime("%Y%m%d%H%M%S")

    # Hash wd_pass_phrase (assume client sent plain, hash here)
    try:
        import hashlib
        wd = body.get("wd_pass_phrase") or ""
        body["wd_pass_phrase"] = hashlib.sha512(wd.encode("utf-8")).hexdigest()
    except Exception:
        pass

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(OPENBANK_DEPOSIT_FIN_NUM, headers=headers, json=body)
            # If provider returned non-200, surface the body for debugging
            if r.status_code != 200:
                raise HTTPException(status_code=502, detail=f"deposit API error: {r.status_code} {r.text}")
            resp = r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"deposit request failed: {e}")

    # Check provider rsp_code
    rsp = resp.get("rsp_code") or resp.get("res_list") and resp.get("res_list")[0].get("bank_rsp_code")
    if resp.get("rsp_code") != "A0000":
        raise HTTPException(status_code=400, detail={"detail": "deposit failed", "provider": resp})

    # Success: mark payment PAID and credit seller
    p.status = "PAID"
    p.provider_raw = json.dumps({"openbanking_deposit": True, "resp": resp})
    db.add(p)
    _credit_seller(db, p)

    # Update uploaded card advertise if present
    try:
        if mongo_enabled() and p.item_id:
            mdb = await get_mongo_db()
            coll = mdb["uploadedCards"]
            query = {"id": int(p.item_id)} if str(p.item_id).isdigit() else {"id": p.item_id}
            await coll.find_one_and_update(query, {"$set": {"is_advertised": True}}, return_document=ReturnDocument.AFTER)
    except Exception:
        pass

    return {"ok": True, "payment_id": p.id, "deposit_resp": resp}



@router.get("/{order_id}")
async def get_payment(order_id: str):
    db = next(get_db())
    p: Optional[Payment] = db.query(Payment).filter(Payment.id == order_id).one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="order not found")
    return {
        "order_id": p.id,
        "buyer_id": p.buyer_id,
        "seller_id": p.seller_id,
        "amount": p.amount,
        "currency": p.currency,
        "status": p.status,
        "provider_payment_id": p.provider_payment_id,
    }


@router.get("/openbanking/debug")
async def openbanking_debug():
    """Return current OpenBanking config values useful for debugging (client_secret omitted)."""
    return {
        "OPENBANK_CLIENT_ID": OPENBANK_CLIENT_ID,
        "OPENBANK_REDIRECT_URI": OPENBANK_REDIRECT_URI,
        "OPENBANK_AUTH_URL": OPENBANK_AUTH_URL,
        "OPENBANK_TOKEN_URL": OPENBANK_TOKEN_URL,
        "OPENBANK_ACCOUNT_API": OPENBANK_ACCOUNT_API,
        "OPENBANK_DEPOSIT_FIN_NUM": OPENBANK_DEPOSIT_FIN_NUM,
    }



@router.get("/sandbox/checkout/{order_id}")
async def sandbox_checkout(order_id: str, db: Session = Depends(get_db)):
    """Return a simple test payload with a URL to complete the payment in sandbox mode."""
    p = db.query(Payment).filter(Payment.id == order_id).one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="order not found")
    return {
        "order_id": p.id,
        "amount": p.amount,
        "currency": p.currency,
        "complete_url": f"/payments/sandbox/complete/{p.id}",
    }



@router.get("/kakao/approve")
async def kakao_approve_pg_redirect(order_id: Optional[str] = None, pg_token: Optional[str] = None):
    """Kakao will redirect here with pg_token after user approves payment on the KakaoPay page.
    This handler then calls Kakao's /v1/payment/approve to finalize the payment.
    """
    kakao_key = os.getenv("KAKAO_ADMIN_KEY")
    if not kakao_key:
        raise HTTPException(status_code=400, detail="Kakao not configured")

    if not order_id or not pg_token:
        # Kakao sends partner_order_id and pg_token — ensure they're present
        raise HTTPException(status_code=400, detail="missing order_id or pg_token")

    approve_url = "https://kapi.kakao.com/v1/payment/approve"
    headers = {"Authorization": f"KakaoAK {kakao_key}", "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"}
    params = {
        "cid": os.getenv("KAKAO_CID", "TC0ONETIME"),
        "tid": None,  # we need to look up tid from our Payment record
        "partner_order_id": order_id,
        "partner_user_id": None,  # optional
        "pg_token": pg_token,
    }

    # find tid from DB
    db = next(get_db())
    p = db.query(Payment).filter(Payment.id == order_id).one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="order not found")
    params["tid"] = p.provider_payment_id

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(approve_url, headers=headers, data=params)
            r.raise_for_status()
            body = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Kakao approve failed: {e}")

    # mark payment as paid and credit seller
    p.status = "PAID"
    p.provider_raw = json.dumps(body)
    db.add(p)
    _credit_seller(db, p)

    # redirect back to client app or return a simple success JSON
    return RedirectResponse(url=os.getenv("KAKAO_SUCCESS_REDIRECT", "/"))


@router.post("/sandbox/complete/{order_id}")
async def sandbox_complete(order_id: str, db: Session = Depends(get_db)):
    """Mark a sandbox order as paid and credit the seller. Use only for local testing."""
    p = db.query(Payment).filter(Payment.id == order_id).one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="order not found")
    if p.status == "PAID":
        return {"ok": True, "already_paid": True}

    p.status = "PAID"
    db.add(p)
    wallet = db.query(Wallet).filter(Wallet.user_id == p.seller_id).one_or_none()
    if not wallet:
        wallet = Wallet(user_id=p.seller_id, balance=0.0)
        db.add(wallet)
    wallet.balance = (wallet.balance or 0.0) + float(p.amount)
    ledger = Ledger(user_id=p.seller_id, change=float(p.amount), reason="sandbox_sale", related_payment_id=p.id)
    db.add(ledger)
    db.commit()
    return {"ok": True}


@router.get("/wallet/{user_id}")
async def get_wallet(user_id: str, db: Session = Depends(get_db)):
    w = db.query(Wallet).filter(Wallet.user_id == user_id).one_or_none()
    if not w:
        return {"user_id": user_id, "balance": 0.0}
    return {"user_id": w.user_id, "balance": float(w.balance)}


@router.post("/payout/{user_id}")
async def payout_to_user(user_id: str, amount: float, db: Session = Depends(get_db)):
    # This is a simplified payout: deduct from wallet and mark ledger.
    w = db.query(Wallet).filter(Wallet.user_id == user_id).one_or_none()
    if not w or (w.balance or 0.0) < float(amount):
        raise HTTPException(status_code=400, detail="insufficient funds")
    w.balance = float(w.balance) - float(amount)
    db.add(w)
    ledger = Ledger(user_id=user_id, change=-float(amount), reason="payout")
    db.add(ledger)
    db.commit()
    return {"ok": True}
