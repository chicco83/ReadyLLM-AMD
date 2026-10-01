"""Servizio di download dei modelli

Scarica in background sulla macchina target i modelli GGUF nella sua models_dir, con ripresa del download.
Il download e' un'operazione lunga: si usa un thread in background + polling dei byte scritti su disco per calcolare l'avanzamento.

Multipiattaforma:
  - Windows: curl.exe (incluso da Win10 1803+) con ripresa del download
  - macOS / Linux：curl -C -
"""

import threading
import time
import uuid
from typing import Optional, List

from .executor import Executor
from .model_catalog import ModelEntry, get_by_id
from ..models.target import Target, get_target

# Tabella dei task: job_id -> {...}
_JOBS: dict = {}
_LOCK = threading.Lock()


def _file_size(executor: Executor, target: Target, path: str) -> int:
    """Interroga i byte attuali di un file sulla macchina target, se non esiste restituisce 0"""
    if target.os == "windows":
        cmd = (f'powershell -Command "if(Test-Path \'{path}\')'
               f'{(chr(123))}(Get-Item \'{path}\').Length{(chr(125))}else{{0}}"')
        result = executor.run(cmd, timeout=10)
    else:
        cmd = f'stat -c %s "{path}" 2>/dev/null || echo 0'
        result = executor.run(cmd, timeout=10)
    digits = "".join(c for c in result.stdout if c.isdigit())
    return int(digits) if digits else 0


def _remote_total_size(executor: Executor, url: str) -> int:
    """Ottiene la dimensione totale del file remoto con una richiesta HEAD (eseguita sulla macchina target, usa la rete della macchina target)"""
    cmd = f'curl -sIL --max-time 20 "{url}" | grep -i content-length | tail -1'
    result = executor.run(cmd, timeout=25)
    digits = "".join(c for c in result.stdout if c.isdigit())
    return int(digits) if digits else 0


def _append_log(job_id: str, msg: str):
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job["logs"].append({"t": time.strftime("%H:%M:%S"), "msg": msg})


def get_job(job_id: str) -> Optional[dict]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs() -> List[dict]:
    with _LOCK:
        return [
            {"job_id": j["job_id"], "model_id": j["model_id"],
             "status": j["status"], "target_id": j["target_id"],
             "downloaded": j["downloaded"], "total": j["total"]}
            for j in _JOBS.values()
        ]


def start_download(target_id: str, model_id: str, source: str = "hf-mirror") -> dict:
    """Avvia il task di download, restituisce {ok, job_id} o {ok:False, message}
    source: huggingface | hf-mirror | modelscope (se ModelScope non ha il repository corrispondente ripiega automaticamente sul mirror)"""
    target = get_target(target_id)
    if not target:
        return {"ok": False, "message": "Macchina target inesistente"}
    if not target.models_dir:
        return {"ok": False, "message": "La macchina target non ha una cartella dei modelli configurata"}
    entry = get_by_id(model_id)
    if not entry:
        return {"ok": False, "message": "Il modello non esiste nel catalogo"}

    url, used_source = entry.resolve(source)
    from .collectors import path_join
    dest = path_join(target, target.models_dir, entry.filename)

    # Se lo stesso file e' gia' in download sulla stessa macchina target lo si riusa
    with _LOCK:
        for j in _JOBS.values():
            if j["target_id"] == target_id and j["dest"] == dest and j["status"] == "downloading":
                return {"ok": True, "job_id": j["job_id"], "reused": True}

        job_id = uuid.uuid4().hex[:8]
        _JOBS[job_id] = {
            "job_id": job_id,
            "model_id": model_id,
            "target_id": target_id,
            "dest": dest,
            "url": url,
            "source": used_source,
            "status": "downloading",
            "downloaded": 0,
            "total": 0,
            "error": "",
            "logs": [],
        }

    def _worker():
        executor = None
        try:
            from .executor import make_executor
            executor = make_executor(target)
            _append_log(job_id, f"Avvio download di {entry.name} {entry.quant} → {dest}")

            total = _remote_total_size(executor, url)
            if total == 0 and entry.size_gb:
                # Se HEAD non e' ottenibile (limiti di rete/reindirizzamento), ripiega sulla dimensione stimata del catalogo
                total = int(entry.size_gb * 1024 * 1024 * 1024)
                _append_log(job_id, "Impossibile ottenere la dimensione esatta, uso la dimensione stimata per calcolare l'avanzamento")
            with _LOCK:
                _JOBS[job_id]["total"] = total
            _append_log(job_id, f"Dimensione totale del file: {round(total/1024/1024,1) if total else 'sconosciuta'} MB")

            # Scarica in un file temporaneo .part e a fine download lo rinomina, evitando che un file incompleto sia scambiato per scaricato
            part_path = dest + ".part"
            if target.os == "windows":
                dl_cmd = (f'curl.exe -L -C - --retry 3 --retry-delay 2 '
                          f'-o "{part_path}" "{url}"')
            else:
                dl_cmd = (f'curl -L -C - --retry 3 --retry-delay 2 '
                          f'-o "{part_path}" "{url}"')

            # Esegue il download in background (senza attendere bloccando), poi fa polling dell'avanzamento
            _append_log(job_id, "Invio della richiesta di download (con ripresa)...")
            result = executor.run(dl_cmd, timeout=7200)

            final_size = _file_size(executor, target, part_path)
            with _LOCK:
                job = _JOBS[job_id]
                job["downloaded"] = final_size

            if result.ok and (total == 0 or final_size >= total):
                # Download completato: rinomina .part → nome finale del file
                if target.os == "windows":
                    mv_cmd = f'move /Y "{part_path}" "{dest}"'
                else:
                    mv_cmd = f'mv -f "{part_path}" "{dest}"'
                mv_result = executor.run(mv_cmd, timeout=10)
                if mv_result.ok:
                    with _LOCK:
                        _JOBS[job_id]["status"] = "success"
                    _append_log(job_id, "✓ Download completato")
                else:
                    with _LOCK:
                        _JOBS[job_id]["status"] = "failed"
                        _JOBS[job_id]["error"] = "Rinomina del file non riuscita: " + (mv_result.stderr or "")
                    _append_log(job_id, f"✗ Rinomina non riuscita: {mv_result.stderr}")
            else:
                with _LOCK:
                    job = _JOBS[job_id]
                    job["status"] = "failed"
                    job["error"] = result.stderr or "Download non completato"
                _append_log(job_id, f"✗ Download non riuscito: {result.stderr}")
        except Exception as e:
            with _LOCK:
                job = _JOBS[job_id]
                job["status"] = "failed"
                job["error"] = str(e)
            _append_log(job_id, f"✗ Eccezione: {e}")
        finally:
            if executor:
                executor.close()

    threading.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "job_id": job_id}


def query_progress(job_id: str) -> Optional[dict]:
    """Interroga l'avanzamento: per i task in downloading, legge in tempo reale i byte del file .part sulla macchina target"""
    job = get_job(job_id)
    if not job:
        return None
    if job["status"] == "downloading":
        target = get_target(job["target_id"])
        if target:
            executor = make_executor_cached(target)
            try:
                # Il file in download si trova al percorso .part
                part_path = job["dest"] + ".part"
                size = _file_size(executor, target, part_path)
                with _LOCK:
                    _JOBS[job_id]["downloaded"] = size
                job["downloaded"] = size
            finally:
                executor.close()
    return job


def make_executor_cached(target: Target):
    """Crea ogni volta un nuovo esecutore (il costo della connessione SSH e' accettabile, evita di condividere il client paramiko tra thread)"""
    from .executor import make_executor
    return make_executor(target)
