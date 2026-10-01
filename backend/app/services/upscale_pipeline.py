"""Servizio di super-risoluzione del video finito: porta un video (o concatenato) a bassa risoluzione interamente a 1080P.

L'endpoint di super-risoluzione di singola immagine /upscale puo' elaborare una sola immagine; per un video serve «estrazione fotogrammi -> super-risoluzione fotogramma per fotogramma -> ricomposizione ->
riapplicazione della traccia audio originale». Questo modulo racchiude la catena batch in un task in background, lo stato e' salvato su disco, l'avanzamento e' consultabile e si puo' riprendere dal punto interrotto.

Flusso di esecuzione (thread in background, fotogramma per fotogramma in serie, perche' la VRAM e' una sola e non si puo' parallelizzare):
  1. ffmpeg sulla macchina target estrae tutti i fotogrammi del video in frame_%05d.png
  2. Invio fotogramma per fotogramma di build_upscale_workflow (RealESRGAN_x4plus -> lanczos fino a
     out_w x out_h), ogni fotogramma ha un filename_prefix univoco (con il numero di fotogramma) per poter ripristinare l'ordine dell'output
  3. ffmpeg sulla macchina target ricompone la sequenza di fotogrammi ingranditi all'fps originale in un video senza audio
  4. La traccia audio del video originale viene rimixata nel video ingrandito (-c:v copy senza ricodifica, -map 1:a? tollera l'assenza di audio)

Punti di progetto (coerenti con video_pipeline):
  - Estrazione, composizione e mix audio avvengono tutti con ffmpeg locale sulla macchina target, senza riportare nulla al controller, per risparmiare traffico.
  - Nessun ambiente personale cablato nel codice: macchina target / porta / cartelle provengono tutte dalla configurazione del Target.
  - Lo stato del task e' salvato in ~/.model-deploy-assistant/upscale_video/, ripristinabile dopo un riavvio.
  - Per ogni fotogramma ingrandito si registra il numero completato, alla ripresa dal punto interrotto viene saltato.
"""

import json
import os
import threading
import time
import uuid
from typing import Optional, Dict, Any, List

from ..models.target import get_target
from .executor import make_executor
from .engine_registry import get_adapter
from .collectors import path_join

_TASK_DIR = os.path.expanduser("~/.model-deploy-assistant/upscale_video")

# Registro in memoria: job_id -> dict del job (salvato anche su disco, dopo il riavvio si ricarica dal disco)
_JOBS: Dict[str, dict] = {}
_LOCK = threading.Lock()


def _task_path(job_id: str) -> str:
    return os.path.join(_TASK_DIR, f"{job_id}.json")


def _save(job: dict):
    os.makedirs(_TASK_DIR, exist_ok=True)
    with open(_task_path(job["job_id"]), "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)


def load_all_jobs():
    """All'avvio del processo ripristina dal disco i task storici (i task in esecuzione, con il thread ormai morto, sono marcati come interrupted)."""
    if not os.path.isdir(_TASK_DIR):
        return
    for name in os.listdir(_TASK_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(_TASK_DIR, name), "r", encoding="utf-8") as f:
                job = json.load(f)
            if job.get("status") == "running":
                job["status"] = "interrupted"
            _JOBS[job["job_id"]] = job
        except Exception:
            continue


def get_job(job_id: str) -> Optional[dict]:
    return _JOBS.get(job_id)


def _comfy_input_dir(target) -> str:
    base = target.engine_path or ""
    return path_join(target, base, "input")


def _comfy_output_dir(target) -> str:
    base = target.engine_path or ""
    return path_join(target, base, "output", "modeldeploy")


def start_upscale_video(target_id: str, filename: str,
                        subfolder: str = "modeldeploy",
                        out_w: int = 1920, out_h: int = 1080,
                        fps: int = 16) -> Dict[str, Any]:
    """Invia un task di super-risoluzione del video finito, restituisce subito job_id, il thread in background avanza fotogramma per fotogramma.

    filename: nome del file video finito in ComfyUI output (es. mdfinal_xxx.mp4).
    subfolder: sottocartella relativa a output, default modeldeploy.
    out_w/out_h: risoluzione di destinazione (default 1920x1080).
    fps: frame rate di composizione, deve coincidere con il video sorgente (per i video lunghi H3 di solito 16).
    """
    target = get_target(target_id)
    if not target:
        return {"success": False, "message": "Macchina target inesistente"}

    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "target_id": target_id,
        "status": "running",
        "src_file": filename,
        "subfolder": subfolder,
        "out_w": out_w, "out_h": out_h, "fps": fps,
        "total_frames": 0,
        "done_frames": 0,
        "done_indices": [],
        "final_file": "",
        "error": "",
        "created_at": time.time(),
    }
    with _LOCK:
        _JOBS[job_id] = job
    _save(job)

    t = threading.Thread(target=_run_upscale, args=(job_id,), daemon=True)
    t.start()
    return {"success": True, "job_id": job_id}


def _run_upscale(job_id: str):
    """Logica principale del thread in background: estrazione -> super-risoluzione fotogramma per fotogramma -> composizione -> riapplicazione audio."""
    job = _JOBS.get(job_id)
    if not job:
        return
    target = get_target(job["target_id"])
    if not target:
        job["status"] = "failed"; job["error"] = "Macchina target inesistente"; _save(job); return

    executor = make_executor(target)
    try:
        engine = get_adapter(executor, target)
        if not hasattr(engine, "build_upscale_workflow"):
            job["status"] = "failed"; job["error"] = "Il motore non supporta la super-risoluzione"; _save(job); return
        if not engine.is_running():
            job["status"] = "failed"; job["error"] = "ComfyUI non e' in esecuzione, avviarlo prima"; _save(job); return

        out_dir = _comfy_output_dir(target)
        input_dir = _comfy_input_dir(target)
        src_path = path_join(target, out_dir if job["subfolder"] == "modeldeploy"
                             else path_join(target, target.engine_path or "", "output", job["subfolder"]),
                             job["src_file"])
        # Cartella di lavoro dei fotogrammi (sotto output/modeldeploy della macchina target)
        frames_dir = path_join(target, out_dir, f"frames_{job_id}")
        ups_dir = path_join(target, out_dir, f"ups_{job_id}")
        executor.run(f'mkdir "{frames_dir}"', timeout=30)
        executor.run(f'mkdir "{ups_dir}"', timeout=30)

        # 1. Estrazione dei fotogrammi: frame_%05d.png
        job["status"] = "extracting"; _save(job)
        r = executor.run(f'ffmpeg -y -i "{src_path}" "{path_join(target, frames_dir, "frame_%05d.png")}"',
                         timeout=180)
        if not r.ok:
            job["status"] = "failed"; job["error"] = "Estrazione dei fotogrammi non riuscita: " + (r.stderr or "")[:200]
            _save(job); return
        # Elenca il numero di fotogrammi
        lr = executor.run(f'dir /b "{path_join(target, frames_dir, "frame_*.png")}"', timeout=30)
        frame_names = [x.strip() for x in (lr.stdout or "").splitlines()
                       if x.strip().lower().endswith(".png")]
        frame_names.sort()
        job["total_frames"] = len(frame_names)
        _save(job)
        if not frame_names:
            job["status"] = "failed"; job["error"] = "Risultato dell'estrazione vuoto"; _save(job); return

        # 2. Super-risoluzione fotogramma per fotogramma: ogni fotogramma ha un prefix univoco per mantenere l'ordine, output ups_%05d_00001_.png
        job["status"] = "upscaling"; _save(job)
        done = set(job.get("done_indices") or [])
        for idx, fname in enumerate(frame_names):
            if idx in done:
                continue
            # Copia i fotogrammi da frames_dir a input (LoadImage riconosce solo la cartella input)
            src_frame = path_join(target, frames_dir, fname)
            in_name = f"mdupf_{job_id}_{idx:05d}.png"
            cp = executor.run(f'copy /Y "{src_frame}" "{path_join(target, input_dir, in_name)}"',
                              timeout=30)
            if not cp.ok:
                job["status"] = "failed"
                job["error"] = f"Copia del fotogramma {idx} in input non riuscita"; _save(job); return
            wf = engine.build_upscale_workflow(
                image_name=in_name, out_w=job["out_w"], out_h=job["out_h"],
                filename_prefix=f"modeldeploy/ups_{job_id}/u{idx:05d}")
            ok, pid = engine.submit_workflow(wf)
            if not ok:
                job["status"] = "failed"; job["error"] = f"Invio del fotogramma {idx} non riuscito: {pid}"
                _save(job); return
            pr = _wait_frame(engine, pid, timeout=120)
            if pr != "completed":
                job["status"] = "failed"
                job["error"] = f"Super-risoluzione del fotogramma {idx}: {pr}"; _save(job); return
            done.add(idx)
            job["done_indices"] = sorted(done)
            job["done_frames"] = len(done)
            _save(job)

        # 3. Composizione del video senza audio: sequenza image2 per numero di fotogramma -> video ingrandito
        job["status"] = "compositing"; _save(job)
        # L'output ingrandito e' in output/modeldeploy/ups_<job>/uNNNNN_00001_.png, va rinominato per allinearlo
        # Usare il pattern di ffmpeg e' scomodo (ComfyUI ha aggiunto il suffisso _00001_), si usa invece una lista concat.
        ups_dir_abs = path_join(target, out_dir, f"ups_{job_id}")
        lu = executor.run(f'dir /b "{path_join(target, ups_dir_abs, "u*.png")}"', timeout=30)
        ups_files = sorted(x.strip() for x in (lu.stdout or "").splitlines()
                           if x.strip().lower().endswith(".png"))
        if len(ups_files) != len(frame_names):
            job["status"] = "failed"
            job["error"] = f"Fotogrammi ingranditi ({len(ups_files)}) != fotogrammi sorgente ({len(frame_names)})"
            _save(job); return
        # Scrivere collegamenti simbolici image2 e' scomodo, si usa il concat demuxer fotogramma per fotogramma (ogni fotogramma duration=1/fps)
        list_name = f"mdupsconcat_{job_id}.txt"
        list_path = path_join(target, out_dir, list_name)
        dur = round(1.0 / max(1, job["fps"]), 6)
        lines = []
        for uf in ups_files:
            lines.append(f"file '{path_join(target, ups_dir_abs, uf)}'")
            lines.append(f"duration {dur}")
        lines.append(f"file '{path_join(target, ups_dir_abs, ups_files[-1])}'")
        if not executor.write_file_bytes("\n".join(lines).encode("utf-8"), list_path):
            job["status"] = "failed"; job["error"] = "Scrittura della lista di composizione non riuscita"; _save(job); return
        silent_name = f"mdupssilent_{job_id}.mp4"
        silent_path = path_join(target, out_dir, silent_name)
        cr = executor.run(
            f'ffmpeg -y -f concat -safe 0 -i "{list_path}" '
            f'-vf "fps={job["fps"]}" '
            f'-c:v libx264 -pix_fmt yuv420p "{silent_path}"', timeout=300)
        if not cr.ok:
            job["status"] = "failed"; job["error"] = "Composizione non riuscita: " + (cr.stderr or "")[:200]
            _save(job); return

        # 4. Riapplicazione della traccia audio originale: video ingrandito + audio del video originale (-map 1:a? tollera l'assenza di audio)
        final_name = f"mdup1080_{job_id}.mp4"
        final_path = path_join(target, out_dir, final_name)
        mr = executor.run(
            f'ffmpeg -y -i "{silent_path}" -i "{src_path}" '
            f'-map 0:v -map 1:a? -c:v copy -c:a aac -shortest "{final_path}"',
            timeout=180)
        if not mr.ok:
            job["status"] = "failed"; job["error"] = "Mix audio non riuscito: " + (mr.stderr or "")[:200]
            _save(job); return

        job["final_file"] = final_name
        job["status"] = "completed"
        _save(job)
    except Exception as e:
        job["status"] = "failed"; job["error"] = f"Eccezione nel task: {e}"; _save(job)
    finally:
        executor.close()


def _wait_frame(engine, prompt_id: str, timeout: int = 120) -> str:
    """Fa polling del task di super-risoluzione di un singolo fotogramma, restituisce completed/error/timeout."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(2)
        try:
            prog = engine.get_progress(prompt_id)
        except Exception:
            continue
        st = prog.get("state")
        if st == "completed":
            return "completed"
        if st == "error":
            return "error"
    return "timeout"
