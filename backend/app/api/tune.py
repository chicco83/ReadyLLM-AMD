"""API di tuning intelligente

Avvia il test di carico di tuning in due fasi (esecuzione in background, restituisce job_id) e interroga avanzamento e risultati con polling.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict

from ..services import tuner, tune_history, tune_log

router = APIRouter()


class TuneRequest(BaseModel):
    target_id: str
    model: str
    ctx_size: int = 8192                 # lunghezza di contesto fissata dall'utente (vincolo, non ottimizzata)
    goal: str = "latency"                # latency | throughput | prefill | coding
    baseline_cfg: Optional[Dict] = None  # parametri attuali dell'utente, misurati prima come baseline di confronto
    # [2026-10-01 v1.1.11] baseline come riga di comando (dal pannello del tuning, modificabile): se presente ha la priorita'
    # su baseline_cfg. Stringa vuota = parametri predefiniti del motore; assente/None = nessuna baseline (o baseline_cfg).
    baseline_args: Optional[str] = None
    model_size_gb: float = 0.0           # GB dei pesi del modello; se 0 il backend lo rileva automaticamente
    try_engines: bool = False            # [2026-10-02 v1.1.27] prova anche le altre build GPU installate (Vulkan <-> ROCm)


@router.post("/start")
def start(req: TuneRequest):
    """Avvia il task di tuning in due fasi"""
    cfg = req.baseline_cfg
    if req.baseline_args is not None:
        cfg = tuner.parse_args_to_cfg(req.baseline_args)
    return tuner.start_tune(
        req.target_id, req.model, req.ctx_size, req.goal,
        cfg, req.model_size_gb, req.try_engines,
    )


@router.get("/baseline")
def baseline(target_id: str, model: str):
    """[2026-10-01 v1.1.11] Baseline proposta per il tuning = parametri del Deploy per questo modello (ultimo tuning o generatore
    deterministico), senza i parametri MTP se modello/build non li supportano. Restituisce anche lo stato MTP."""
    from . import deploy
    from ..models.target import get_target
    from ..services.executor import make_executor
    target = get_target(target_id)
    if not target:
        return {"ok": False, "message": "Macchina target inesistente"}
    d = deploy.default_args(target_id, model)
    cfg = tuner.parse_args_to_cfg(d.get("args", ""))
    ex = make_executor(target)
    try:
        mtp = tuner.mtp_state(ex, target, model)
    finally:
        ex.close()
    if not mtp["allowed"]:
        cfg = tuner.strip_mtp(cfg)
    return {"ok": True, "args": tuner.cfg_to_args(cfg), "source": d.get("source", "default"),
            "score": d.get("score", 0), "mtp": mtp}


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


@router.post("/cancel")
def cancel(target_id: str):
    """[2026-10-02 v1.1.33] Ferma il tuning in corso: lo segna come annullato e ferma llama-server cosi' la prova in corso termina subito"""
    n = tuner.cancel_job(target_id)
    if n:
        try:
            from . import deploy
            deploy.stop_model(target_id)
        except Exception:
            pass
    return {"ok": True, "cancelled": n}


@router.get("/last")
def last(target_id: str):
    """[2026-10-02 v1.1.23] Ultimo tuning della macchina (anche concluso): ripristina esito/risultati dopo un rimontaggio della pagina"""
    return {"job": tuner.get_last_job(target_id)}


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


@router.get("/history")
def history(target_id: str = ""):
    """[2026-10-02 v1.1.24] Storico di tutte le ottimizzazioni concluse (piu' recenti per prime), filtrabile per macchina"""
    return {"entries": tune_log.list_entries(target_id)}


@router.delete("/history/{entry_id}")
def history_delete(entry_id: str):
    """Elimina una voce dello storico"""
    return {"ok": tune_log.delete_entry(entry_id)}


class ApplyRequest(BaseModel):
    target_id: str
    model: str
    ctx_size: int
    params: Dict[str, str]
    score: float = 0.0                  # t/s di decodifica misurati (mostrati dal Deploy)
    engine_path: Optional[str] = None   # se il tuning consiglia un altro motore
    engine_backend: Optional[str] = None
    restart: bool = True                # riavvia subito il modello con la configurazione consigliata


@router.post("/apply")
def apply(req: ApplyRequest):
    """[2026-10-02 v1.1.27] «Salva e applica»: salva i parametri, se consigliato cambia motore (engine_path) e riavvia il modello
    (il tuning lascia il server fermo). Restituisce l'esito dell'avvio."""
    from ..models.target import get_target, upsert_target
    from . import deploy
    if not req.params:
        return {"ok": False, "message": "Nessun parametro da salvare"}
    tune_history.save_latest(req.target_id, req.model, req.ctx_size, req.params, source="tuner", score=req.score)
    target = get_target(req.target_id)
    if not target:
        return {"ok": False, "message": "Macchina target inesistente"}
    switched = ""
    if req.engine_path and req.engine_path != target.engine_path:
        target.engine_path = req.engine_path
        be = (req.engine_backend or "").lower().rstrip("?")
        if be in ("rocm", "vulkan", "cuda", "cpu"):
            target.llama_backend = be
        upsert_target(target)
        switched = req.engine_backend or req.engine_path
    if not req.restart:
        return {"ok": True, "switched": switched, "started": False, "message": "Salvato"}
    # stesso insieme di parametri misurato dal tuner (compresi flash-attn e fit off), cosi' le prestazioni coincidono
    args = tuner._args_list(dict(req.params), target, req.ctx_size)
    deploy.stop_model(req.target_id)
    res = deploy.start_model(deploy.DeployRequest(target_id=req.target_id, model=req.model, extra_args=args))
    return {"ok": bool(res.get("success")), "switched": switched, "started": bool(res.get("success")),
            "message": res.get("message", ""), "args": res.get("args")}
