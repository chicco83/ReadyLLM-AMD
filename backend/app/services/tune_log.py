"""Storico completo delle ottimizzazioni (tuning) — v1.1.24, 2026-10-02

Diversamente da tune_history.py (che conserva solo l'ULTIMO risultato per macchina+modello, per precompilare il Deploy),
qui si accoda UNA voce per ogni tuning concluso (riuscito o fallito), cosi' si possono confrontare nel tempo modelli, motori,
backend e prestazioni.

Archivio: ~/.model-deploy-assistant/tune_log.json  -> lista di voci, la piu' recente in coda; massimo MAX_ENTRIES voci.
Voce: {id, ts_start, ts_end, duration_s, status, error, target_id, target_name, os, model, model_size_gb, ctx_size, goal,
       engine{backend,version,path}, gpu{name,vram_gb}, trials, baseline{...}, best{...}, gain_pct}
dove baseline/best = {label, config, metrics{decode,prefill,ttft_ms,gpu_util,gpu_mem_pct}, score}.
"""

import json
import os
import threading
from typing import List, Optional

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".model-deploy-assistant")
LOG_FILE = os.path.join(CONFIG_DIR, "tune_log.json")
MAX_ENTRIES = 500
_LOCK = threading.Lock()


def _load() -> List[dict]:
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save(entries: List[dict]) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = LOG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LOG_FILE)


def _slim(r: Optional[dict]) -> Optional[dict]:
    """Riduce un risultato di prova ai soli campi utili allo storico"""
    if not r:
        return None
    return {"label": r.get("label", ""), "config": r.get("config", {}),
            "metrics": r.get("metrics", {}), "score": r.get("score", 0)}


def add_entry(job: dict) -> None:
    """Registra nello storico un job di tuning concluso (chiamata da tuner._finalize/_fail). Non solleva eccezioni."""
    try:
        import time
        meta = job.get("meta", {})
        base, best = _slim(job.get("baseline")), _slim(job.get("best"))
        gain = None
        if base and best and base["metrics"].get("decode", 0) > 0:
            gain = round((best["metrics"].get("decode", 0) - base["metrics"]["decode"]) / base["metrics"]["decode"] * 100, 1)
        t0 = job.get("ts_start", time.time())
        entry = {
            "id": job["job_id"], "ts_start": t0, "ts_end": time.time(), "duration_s": round(time.time() - t0),
            "status": job.get("status", ""), "error": job.get("error", ""),
            "target_id": job.get("target_id", ""), "target_name": meta.get("target_name", ""), "os": meta.get("os", ""),
            "model": job.get("model", ""), "model_size_gb": meta.get("model_size_gb", 0),
            "ctx_size": job.get("ctx_size", 0), "goal": job.get("goal", ""),
            "engine": meta.get("engine", {}), "gpu": meta.get("gpu", {}),
            "trials": job.get("progress", {}).get("done", 0),
            "baseline": base, "best": best, "gain_pct": gain,
        }
        with _LOCK:
            entries = _load()
            entries = [e for e in entries if e.get("id") != entry["id"]] + [entry]
            _save(entries[-MAX_ENTRIES:])
    except Exception:
        pass


def list_entries(target_id: str = "") -> List[dict]:
    """Voci dello storico, dalla piu' recente; opzionalmente filtrate per macchina"""
    with _LOCK:
        entries = _load()
    if target_id:
        entries = [e for e in entries if e.get("target_id") == target_id]
    return list(reversed(entries))


def delete_entry(entry_id: str) -> bool:
    with _LOCK:
        entries = _load()
        new = [e for e in entries if e.get("id") != entry_id]
        if len(new) == len(entries):
            return False
        _save(new)
        return True
