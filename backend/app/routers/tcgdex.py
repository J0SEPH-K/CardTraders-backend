from typing import Any, Dict, List, Optional
import asyncio

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

API_BASE = "https://api.tcgdex.net/v2"


def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "CardTraders/1.0 (+https://cardtraders.app)"
    }


@router.get("/cards/search")
async def search_cards(
    q: str = Query("", description="Name filter for cards (laxist by default)"),
    lang: str = Query("en", description="Language code, e.g., en, ja, fr, de, es, it, pt-br, th, id, zh-tw"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(30, ge=1, le=100),
    enrich: bool = Query(True, description="Whether to enrich results with set and rarity"),
):
    params: Dict[str, Any] = {}
    if q:
        params["name"] = q
    # TCGdex pagination uses namespaced params
    params["pagination:page"] = page
    params["pagination:itemsPerPage"] = pageSize

    url = f"{API_BASE}/{lang}/cards"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0)) as client:
            r = await client.get(url, params=params, headers=_headers())
            # Some gateways might dislike colon params, fallback without pagination if 4xx
            if r.status_code >= 400 and r.status_code < 500 and ("pagination" in "&".join(params.keys())):
                fallback_params = {k: v for k, v in params.items() if not k.startswith("pagination:")}
                r = await client.get(url, params=fallback_params, headers=_headers())

            if r.status_code != 200:
                # Return upstream body for easier debugging
                text = r.text
                raise HTTPException(status_code=r.status_code, detail=text or f"TCGdex non-200: {r.status_code}")

            results: Any = r.json()

            # Optionally enrich each card with set and rarity (best-effort, ignore per-item failures)
            if enrich and isinstance(results, list) and results:
                sem = asyncio.Semaphore(8)

                async def fetch_extra(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                    cid = card.get("id")
                    if not cid:
                        return None
                    detail_url = f"{API_BASE}/{lang}/cards/{cid}"
                    try:
                        async with sem:
                            rd = await client.get(detail_url, headers=_headers())
                        if rd.status_code != 200:
                            return None
                        det = rd.json()
                        set_obj = det.get("set") or {}
                        set_brief = None
                        if isinstance(set_obj, dict):
                            sid = set_obj.get("id")
                            sname = set_obj.get("name")
                            if sid or sname:
                                set_brief = {"id": sid, "name": sname}
                        rarity = det.get("rarity")
                        return {"id": cid, "set": set_brief, "rarity": rarity}
                    except Exception:
                        return None

                extras: List[Optional[Dict[str, Any]]] = await asyncio.gather(
                    *(fetch_extra(card) for card in results), return_exceptions=False
                )
                # Map by id and merge
                extras_map: Dict[str, Dict[str, Any]] = {}
                for ex in extras:
                    if ex and ex.get("id"):
                        extras_map[ex["id"]] = ex
                for card in results:
                    cid = card.get("id")
                    if not cid:
                        continue
                    ex = extras_map.get(cid)
                    if not ex:
                        continue
                    if ex.get("set") is not None:
                        card["set"] = ex["set"]
                    if ex.get("rarity"):
                        card["rarity"] = ex["rarity"]

            return results
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"TCGdex timeout: {e!s}") from e
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"TCGdex upstream error: {e.__class__.__name__}: {e!s}") from e
    # unreachable (returns inside async with), but keep for safety
    return []


@router.get("/cards/{card_id}")
async def get_card(card_id: str, lang: str = Query("en")):
    url = f"{API_BASE}/{lang}/cards/{card_id}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0)) as client:
            r = await client.get(url, headers=_headers())
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"TCGdex timeout: {e!s}") from e
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"TCGdex upstream error: {e.__class__.__name__}: {e!s}") from e

    if r.status_code != 200:
        text = r.text
        raise HTTPException(status_code=r.status_code, detail=text or f"TCGdex non-200: {r.status_code}")

    return r.json()
