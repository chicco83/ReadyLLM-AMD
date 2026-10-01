"""API di monitoraggio + push via WebSocket (basata sul Target configurato dall'utente)"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException

from ..config import REFRESH_INTERVAL
from ..models.target import get_target
from ..services.executor import make_executor
from ..services.collectors import collect_all
from ..services.token_stats import record_tokens, get_daily_stats, get_total_stats

router = APIRouter()


@router.get("/snapshot")
def get_snapshot(target_id: str):
    """Restituisce un'istantanea di monitoraggio"""
    target = get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Macchina target inesistente, configurarla prima nelle Impostazioni")
    executor = make_executor(target)
    try:
        data = collect_all(executor, target)
        record_tokens(target_id, data.get("metrics", {}))
        return data
    finally:
        executor.close()


@router.get("/token-stats")
def get_token_stats(target_id: str, days: int = 14):
    """Statistiche giornaliere di utilizzo token (input/output), persistite, non si perdono al riavvio"""
    return get_daily_stats(target_id, days)


@router.get("/token-total")
def get_token_total(target_id: str):
    """Token consumati cumulati (somma di tutti i giorni registrati)"""
    return get_total_stats(target_id)


@router.websocket("/ws")
async def monitor_websocket(ws: WebSocket, target_id: str = ""):
    """Push in tempo reale dei dati di monitoraggio via WebSocket"""
    await ws.accept()

    target = get_target(target_id)
    if not target:
        await ws.send_text(json.dumps({"error": "Macchina target inesistente"}))
        await ws.close()
        return

    executor = make_executor(target)
    try:
        while True:
            data = await asyncio.to_thread(collect_all, executor, target)
            await asyncio.to_thread(record_tokens, target_id, data.get("metrics", {}))
            await ws.send_text(json.dumps(data, ensure_ascii=False))
            await asyncio.sleep(REFRESH_INTERVAL / 1000)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        executor.close()
