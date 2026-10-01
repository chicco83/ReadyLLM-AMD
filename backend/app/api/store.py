"""API del negozio di modelli

Fornisce la navigazione del catalogo di modelli integrato, il filtro per VRAM della macchina target, l'avvio dei download, l'interrogazione dell'avanzamento
e l'elenco dei modelli scaricati. Il download viene eseguito sulla macchina target e supporta la ripresa.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from ..services.model_catalog import list_all, filter_by_vram, get_by_id, fetch_dynamic_catalog
from ..services import downloader
from ..models.target import get_target
from ..services.executor import make_executor

router = APIRouter()


@router.get("/jobs")
def active_jobs(target_id: Optional[str] = None):
    """Elenca tutti i task di download (anche in corso); il frontend li usa per ripristinare l'avanzamento dopo un refresh"""
    jobs = downloader.list_jobs()
    if target_id:
        jobs = [j for j in jobs if j["target_id"] == target_id]
    return {"jobs": jobs}


@router.get("/models")
def models(target_id: Optional[str] = None, source: str = "hf-mirror",
           category: Optional[str] = None):
    """Elenco dei modelli; category=text/video filtra per categoria; dato target_id indica in base alla VRAM
    di quella macchina se il modello puo' girare, source sceglie la sorgente di download"""
    items = list_all(source, category)
    if target_id:
        target = get_target(target_id)
        vram = 0.0
        if target:
            executor = make_executor(target)
            try:
                from ..services.collectors import _detect_gpu_static, _detect_memory_static
                gpu = _detect_gpu_static(executor, target)
                if gpu:
                    vram = gpu.get("total_memory_gb", 0)
                    # Memoria unificata Apple Silicon: VRAM utilizzabile ~ 75% della memoria fisica (limite predefinito di Metal)
                    if gpu.get("unified") or vram == 0:
                        mem = _detect_memory_static(executor, target)
                        vram = mem.get("total_gb", 0) * 0.75
            finally:
                executor.close()
        for it in items:
            it["fits"] = it["min_vram_gb"] <= vram if vram > 0 else None
    return {"models": items}


@router.get("/refresh")
def refresh_catalog():
    """Aggiornamento manuale: ottiene da HuggingFace i modelli GGUF popolari piu' recenti"""
    result = fetch_dynamic_catalog(force=True)
    return result


@router.get("/dynamic")
def dynamic_models():
    """Ottiene l'elenco dinamico dei modelli (usa la cache se presente)"""
    result = fetch_dynamic_catalog(force=False)
    return result


class DownloadRequest(BaseModel):
    target_id: str
    model_id: str
    source: str = "hf-mirror"  # huggingface | hf-mirror | modelscope


@router.post("/download")
def download(req: DownloadRequest):
    """Avvia il task di download (source sceglie la sorgente; se ModelScope non ha il repository corrispondente si ripiega automaticamente sul mirror)"""
    return downloader.start_download(req.target_id, req.model_id, req.source)


@router.get("/download/{job_id}")
def download_status(job_id: str):
    """Interroga l'avanzamento del download (legge in tempo reale i byte gia' scritti su disco dalla macchina target)"""
    job = downloader.query_progress(job_id)
    if not job:
        return {"status": "not_found", "logs": []}
    total = job.get("total", 0)
    done = job.get("downloaded", 0)
    job["percent"] = round(done / total * 100, 1) if total > 0 else 0
    return job


@router.get("/downloaded")
def downloaded(target_id: str):
    """Elenca i file .gguf nella cartella dei modelli della macchina target (con la dimensione reale); il frontend li usa per verificarne l'integrita'"""
    target = get_target(target_id)
    if not target or not target.models_dir:
        return {"models": [], "error": "Cartella dei modelli non configurata" if not (target and target.models_dir) else "Macchina target inesistente"}
    executor = make_executor(target)
    try:
        if target.os == "windows":
            # Formato di output "nomefile|byte"
            # [2026-10-01 v1.1.0] -Recurse: cerca anche nelle sottocartelle.
            # Versione precedente (sostituita): Get-ChildItem '<dir>\\*.gguf' (solo primo livello)
            cmd = (f'powershell -Command "Get-ChildItem \'{target.models_dir}\' -Recurse -Filter *.gguf '
                   f'| ForEach-Object {{ $_.Name + \'|\' + $_.Length }}"')
            result = executor.run(cmd, timeout=15)
        else:
            result = executor.run(
                # [2026-10-01 v1.1.0] Ricorsivo (era: find ... -maxdepth 1 -name "*.gguf")
                f'find -L "{target.models_dir}" -maxdepth 8 -iname "*.gguf" -printf "%f|%s\\n" 2>/dev/null '
                f'|| stat -f "%N|%z" "{target.models_dir}"/*.gguf 2>/dev/null',
                timeout=15)
        files = []
        if result.ok and result.stdout:
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.rsplit("|", 1)
                if len(parts) == 2:
                    fname = parts[0].split("\\")[-1].split("/")[-1]
                    try:
                        size_bytes = int(parts[1])
                    except ValueError:
                        size_bytes = 0
                else:
                    fname = line.split("\\")[-1].split("/")[-1]
                    size_bytes = 0
                files.append((fname, size_bytes))
        # Confronto con il catalogo, indicazione dell'integrita'
        catalog_names = {m["filename"]: m for m in list_all()}
        models = []
        for fname, size_bytes in files:
            entry = catalog_names.get(fname)
            expected_gb = entry["size_gb"] if entry else None
            size_gb = round(size_bytes / (1024 ** 3), 2)
            # Valutazione di integrita': se il catalogo indica una dimensione attesa, una dimensione reale >= 90% di quella attesa vale come completo
            complete = True
            if expected_gb and expected_gb > 0:
                complete = size_gb >= expected_gb * 0.9
            models.append({
                "filename": fname,
                "size_bytes": size_bytes,
                "size_gb": size_gb,
                "expected_gb": expected_gb,
                "complete": complete,
                "in_catalog": bool(entry),
                "model_id": entry["id"] if entry else None,
            })
        return {"models": models, "count": len(models)}
    finally:
        executor.close()
