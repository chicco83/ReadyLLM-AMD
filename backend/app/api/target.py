"""API di configurazione delle macchine target

Qui l'utente aggiunge/gestisce le macchine target locali o della rete locale, indicando tipo di sistema, percorso del motore,
cartella dei modelli e porta. Tutte le funzioni operano su queste configurazioni.
"""

import platform

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional


def _detect_local_os() -> str:
    """Rileva il sistema operativo della macchina che esegue il backend: darwin->macos, windows->windows, altri->linux"""
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s == "windows":
        return "windows"
    return "linux"

from ..models.target import (
    Target, load_targets, upsert_target, delete_target, get_target,
)
from ..services.executor import make_executor
from ..services.collectors import detect_hardware
from ..services import installer

router = APIRouter()


class TargetRequest(BaseModel):
    conn_type: str = "local"
    host: str = ""
    port: int = 22
    user: str = ""
    auth_type: str = "key"
    key_path: str = ""
    password: str = ""
    os: str = "linux"
    engine_type: str = "llama_cpp"
    engine_path: str = ""
    llama_backend: str = "auto"   # [2026-10-01 v1.1.0] auto|cuda|rocm|vulkan|cpu
    models_dir: str = ""
    service_port: int = 8080
    id: Optional[str] = None
    name: str = "Locale"


@router.get("")
def list_targets():
    """Elenca tutte le macchine target configurate"""
    return {"targets": [t.to_dict() for t in load_targets()]}


@router.post("")
def create_target(req: TargetRequest):
    """Aggiunge o aggiorna una macchina target"""
    data = req.model_dump()
    if not data.get("id"):
        data.pop("id", None)
    target = Target(**data)
    # [2026-10-01 v1.1.0] match_identity=True: evita duplicati se manca l'id (bug targets.json)
    targets = upsert_target(target, match_identity=True)
    return {"ok": True, "id": target.id, "targets": [t.to_dict() for t in targets]}


@router.delete("/{target_id}")
def remove_target(target_id: str):
    """Elimina una macchina target"""
    targets = delete_target(target_id)
    return {"ok": True, "targets": [t.to_dict() for t in targets]}


@router.post("/test")
def test_connection(req: TargetRequest):
    """Verifica la connessione e restituisce le informazioni hardware (senza salvare su disco)"""
    data = req.model_dump()
    data.pop("id", None)
    data.pop("name", None)
    target = Target(**data)
    executor = make_executor(target)
    try:
        # Prima si verifica la connettivita' di base
        probe = "echo OK" if target.os != "windows" else 'echo OK'
        r = executor.run(probe, timeout=10)
        if not r.ok and "OK" not in r.stdout:
            return {"ok": False, "message": f"Connessione non riuscita: {r.stderr or r.stdout}"}
        hw = detect_hardware(executor, target)
        return {"ok": True, "message": "Connessione riuscita", "hardware": hw}
    except Exception as e:
        return {"ok": False, "message": f"Errore di connessione: {e}"}
    finally:
        executor.close()



@router.get("/{target_id}/engine")
def check_engine(target_id: str):
    """Rileva se sulla macchina target e' installato il motore di inferenza"""
    target = get_target(target_id)
    if not target:
        return {"installed": False, "reason": "Macchina target inesistente"}
    executor = make_executor(target)
    try:
        return installer.detect_engine(executor, target)
    finally:
        executor.close()


class InstallRequest(BaseModel):
    target_id: str


@router.post("/install-engine")
def install_engine(req: InstallRequest):
    """Avvia l'installazione con un clic del motore di inferenza (task in background, restituisce job_id per il polling)"""
    target = get_target(req.target_id)
    if not target:
        return {"ok": False, "message": "Macchina target inesistente"}
    job_id = installer.start_install(target)
    return {"ok": True, "job_id": job_id}


@router.get("/install-status/{job_id}")
def install_status(job_id: str):
    """Interroga stato e log del task di installazione"""
    job = installer.get_job(job_id)
    if not job:
        return {"status": "not_found", "logs": []}
    return job



@router.get("/local-os")
def local_os():
    """Restituisce il sistema operativo della macchina che esegue il backend, cosi' il frontend in modalita' «locale» lo riconosce da solo, senza scelta manuale"""
    return {"os": _detect_local_os()}



@router.get("/engines")
def list_engines():
    """Restituisce i metadati di tutti i motori di inferenza disponibili (nome/piattaforme supportate/formato modelli/suggerimenti di installazione), per il rendering dinamico del frontend"""
    from ..services.engine_registry import list_engines as _list
    return {"engines": _list()}
