"""API di tuning intelligente

Avvia il test di carico di tuning in due fasi (esecuzione in background, restituisce job_id) e interroga avanzamento e risultati con polling.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict

from ..services import tuner, tune_history

router = APIRouter()


class TuneRequest(BaseModel):
    target_id: str
    model: str
    ctx_size: int = 8192                 # lunghezza di contesto fissata dall'utente (vincolo, non ottimizzata)
    goal: str = "latency"                # latency | throughput | prefill
    baseline_cfg: Optional[Dict] = None  # parametri attuali dell'utente, misurati prima come baseline di confronto
    model_size_gb: float = 0.0           # GB dei pesi del modello; se 0 il backend lo rileva automaticamente


@router.post("/start")
def start(req: TuneRequest):
    """Avvia il task di tuning in due fasi"""
    return tuner.start_tune(
        req.target_id, req.model, req.ctx_size, req.goal,
        req.baseline_cfg, req.model_size_gb,
    )


@router.get("/status/{job_id}")
def status(job_id: str):
    """Interroga avanzamento e risultati del tuning"""
    job = tuner.get_job(job_id)
    if not job:
        return {"status": "not_found", "logs": [], "results": []}
    return job


@router.get("/active")
def active(target_id: str):
    """Restituisce il task di tuning in corso sulla macchina target, per riprendere il polling dopo un refresh del frontend"""
    return {"jobs": tuner.list_active_jobs(target_id)}


@router.get("/options")
def options():
    """Restituisce gli obiettivi di ottimizzazione disponibili e gli intervalli dei parametri baseline, per il rendering del frontend"""
    return {
        "goals": [{"value": k, "label": v} for k, v in tuner.GOAL_LABELS.items()],
        "spec_options": tuner.SPEC_OPTIONS,
        "cache_options": tuner.CACHE_OPTIONS,
        "ngl_options": tuner.NGL_OPTIONS,
    }



class SaveTuneRequest(BaseModel):
    target_id: str
    model: str
    ctx_size: int                        # lunghezza di contesto fissa, salvata insieme ai parametri
    params: Dict[str, str]               # parametri ottimali (dizionario piatto, senza ctx-size)
    score: float = 0.0


@router.post("/save")
def save(req: SaveTuneRequest):
    """Salva sul modello i parametri ottimali di un tuning (incluso ctx_size fisso),
    come sorgente per precompilare i default-args della pagina Deploy. Chiamata quando l'utente clicca «Salva e applica» nella schermata dei risultati."""
    if not req.params:
        return {"ok": False, "message": "Nessun parametro da salvare"}
    tune_history.save_latest(
        req.target_id, req.model, req.ctx_size, req.params,
        source="tuner", score=req.score,
    )
    return {"ok": True, "message": f"Salvato nei parametri di deploy di {req.model} (con ctx={req.ctx_size})"}
