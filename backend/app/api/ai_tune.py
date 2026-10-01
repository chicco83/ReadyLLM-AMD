"""API di tuning con AI Agent

Gestione della configurazione + avvio del task di tuning dell'Agent + polling dell'avanzamento.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict

from ..services import ai_tuner, tune_history

router = APIRouter()


class AIConfigRequest(BaseModel):
    api_url: str
    api_key: str = ""
    model_name: str = ""


class AITuneRequest(BaseModel):
    target_id: str
    model: str
    ctx_size: int = 8192
    goal: str = "latency"       # latency | throughput | prefill
    user_desc: str = ""         # descrizione dello scenario d'uso (facoltativa)


@router.get("/config")
def get_config():
    """Restituisce la configurazione AI (api_key mascherata)"""
    cfg = ai_tuner.get_config()
    masked = dict(cfg)
    if masked.get("api_key"):
        k = masked["api_key"]
        masked["api_key"] = k[:4] + "***" + k[-4:] if len(k) > 8 else "***"
    return masked


@router.put("/config")
def put_config(req: AIConfigRequest):
    """Salva la configurazione AI.

    Evita la trappola «eco mascherato + sovrascrittura totale»: la api_key restituita da get_config
    e' una stringa mascherata (es. sk-w***LXcQ) che il frontend rimette nel form. Se l'utente
    modifica solo url/model senza toccare la chiave e invia, la stringa mascherata
    sovrascriverebbe la chiave reale nel file.
    Per questo, quando la api_key inviata e' vuota, contiene il marcatore *** o coincide col valore
    mascherato attuale, si conserva la chiave reale gia' presente nel file, senza mai sovrascriverla."""
    existing = ai_tuner.get_config()
    real_key = existing.get("api_key", "")
    masked = (real_key[:4] + "***" + real_key[-4:]) if len(real_key) > 8 else "***"
    new_key = req.api_key
    if not new_key or new_key == masked or "***" in new_key:
        new_key = real_key  # l'utente non ha cambiato la chiave davvero, si mantiene il valore originale
    ai_tuner.save_config({
        "api_url": req.api_url,
        "api_key": new_key,
        "model_name": req.model_name,
    })
    return {"ok": True}


@router.post("/test-connection")
def test_connection(req: AIConfigRequest):
    """Verifica la connettivita' dell'API LLM"""
    return ai_tuner.test_connection({
        "api_url": req.api_url,
        "api_key": req.api_key,
        "model_name": req.model_name,
    })


@router.post("/start")
def start(req: AITuneRequest):
    """Avvia il task di tuning con AI Agent"""
    return ai_tuner.start_ai_tune(
        req.target_id, req.model, req.ctx_size, req.goal, req.user_desc,
    )


@router.get("/status/{job_id}")
def status(job_id: str):
    """Interroga avanzamento e risultato del tuning AI"""
    job = ai_tuner.get_job(job_id)
    if not job:
        return {"status": "not_found", "logs": [], "rounds": []}
    return job


@router.get("/active")
def active(target_id: str):
    """Restituisce il task di tuning AI in corso sulla macchina target, per riprendere il polling dopo un refresh del frontend"""
    return {"jobs": ai_tuner.list_active_jobs(target_id)}



class SaveTuneRequest(BaseModel):
    target_id: str
    model: str
    ctx_size: int                        # lunghezza di contesto fissa, salvata insieme ai parametri
    params: Dict[str, str]               # parametri ottimali (dizionario piatto, senza ctx-size)
    score: float = 0.0


@router.post("/save")
def save(req: SaveTuneRequest):
    """Salva sul modello i parametri finali raccomandati dal tuning AI (incluso ctx_size fisso),
    come sorgente per precompilare i default-args della pagina Deploy. Chiamata quando l'utente clicca «Salva e applica» nella schermata dei risultati."""
    if not req.params:
        return {"ok": False, "message": "Nessun parametro da salvare"}
    tune_history.save_latest(
        req.target_id, req.model, req.ctx_size, req.params,
        source="ai_tuner", score=req.score,
    )
    return {"ok": True, "message": f"Salvato nei parametri di deploy di {req.model} (con ctx={req.ctx_size})"}
