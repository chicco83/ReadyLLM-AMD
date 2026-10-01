"""Persistenza dei risultati di tuning

I parametri raccomandati dal tuning intelligente (tuner / ai_tuner) prima esistevano solo in memoria in _JOBS e andavano persi al riavvio del backend.
Questo modulo salva su disco i «parametri ottimali» di ogni tuning per (macchina target, modello), cosi' la pagina Deploy li usa per precompilare i parametri di default.

Archivio: ~/.model-deploy-assistant/tune_history.json
Struttura: { "<target_id>::<model>": {params, ctx_size, source, score, ts} }
Per la stessa macchina e lo stesso modello si conserva solo l'ultimo risultato (sovrascrittura).

params e' un dizionario piatto {nome parametro: valore}, i nomi non hanno il prefisso -- e non includono ctx-size (salvato a parte).
"""

import json
import os
import time
import threading
from typing import Optional

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".model-deploy-assistant")
HISTORY_FILE = os.path.join(CONFIG_DIR, "tune_history.json")

_LOCK = threading.Lock()


def _key(target_id: str, model: str) -> str:
    return f"{target_id}::{model}"


def _load() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HISTORY_FILE)


def save_latest(
    target_id: str,
    model: str,
    ctx_size: int,
    params: dict,
    source: str = "tuner",
    score: float = 0.0,
) -> None:
    """Registra gli ultimi parametri ottimali di tuning di una macchina target + modello (sovrascrittura).

    Args:
        params: dizionario piatto di parametri {nome parametro: valore}, senza ctx-size
        source: 'tuner' (tuning automatico) o 'ai_tuner' (tuning AI)
        score: punteggio di velocita' misurato di quella configurazione (t/s), mostrato dal frontend
    """
    if not params:
        return
    with _LOCK:
        data = _load()
        data[_key(target_id, model)] = {
            "params": {k: str(v) for k, v in params.items()},
            "ctx_size": int(ctx_size),
            "source": source,
            "score": round(float(score), 2),
            "score_kind": "decode_tps",      # [2026-10-02 v1.1.30] t/s di decodifica (i record senza questo campo hanno il punteggio composito)
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save(data)


def get_latest(target_id: str, model: str) -> Optional[dict]:
    """Recupera gli ultimi parametri di tuning di una macchina target + modello.

    Alla restituzione unisce ctx_size in params (chiave 'ctx-size'), cosi' il frontend puo' rendere direttamente la riga di comando completa.
    Se non c'e' alcun record restituisce None.
    """
    with _LOCK:
        rec = _load().get(_key(target_id, model))
    if not rec:
        return None
    params = dict(rec.get("params", {}))
    if rec.get("ctx_size"):
        params["ctx-size"] = str(rec["ctx_size"])
    return {
        "params": params,
        "ctx_size": rec.get("ctx_size", 0),
        "source": rec.get("source", ""),
        # [2026-10-02 v1.1.30] I record vecchi (senza score_kind) hanno il punteggio composito del tuning, non i t/s: mostrarlo come
        # «misurati 42.61 t/s» era sbagliato. Si usa la decodifica dello storico tuning se c'e', altrimenti 0 (il Deploy non lo mostra).
        # Versione precedente: "score": rec.get("score", 0)
        "score": rec.get("score", 0) if rec.get("score_kind") == "decode_tps" else _legacy_decode(target_id, model),
        "ts": rec.get("ts", ""),
    }


def _legacy_decode(target_id: str, model: str) -> float:
    try:
        from .tune_log import last_decode
        return last_decode(target_id, model)
    except Exception:
        return 0.0


def list_history() -> dict:
    """Restituisce tutta la cronologia (per debug/visualizzazione)"""
    with _LOCK:
        return _load()
