"""Servizio di orchestrazione dei video lunghi (livelli 3, 4 e 5 dei video lunghi in uno)

Trasforma uno storyboard (prodotto da video_storyboard) in un video lungo di minuti. Idea di fondo:
H3 puo' generare solo 5-15 secondi per volta, quindi un video lungo richiede «generazione di piu' segmenti in serie + raccordo con primo/ultimo fotogramma + concatenazione».

Flusso di esecuzione (thread in background, segmento per segmento in serie, perche' la VRAM e' una sola e non si puo' parallelizzare):
  Segmento 0: con l'«immagine del primo fotogramma seed» (generata dall'assistente o caricata dall'utente) si esegue I2V -> si ottiene shot_0.mp4
  Segmento i>0: sulla macchina target con ffmpeg si estrae l'ultimo fotogramma di shot_(i-1) in input/ -> lo si usa come primo fotogramma di questo segmento per
          I2V -> si ottiene shot_i.mp4 (l'immagine si raccorda cosi' senza stacchi con il segmento precedente)
  A tutto completato: ffmpeg concat sulla macchina target di tutti i segmenti -> final.mp4

Punti di progetto:
  - Lo stato del task e' salvato in ~/.model-deploy-assistant/long_video/, dopo un riavvio del processo / refresh del frontend si puo' ripristinare,
    i segmenti gia' completati non vengono rieseguiti (ripresa dal punto interrotto).
  - Estrazione dei fotogrammi e concatenazione avvengono tutte con ffmpeg locale sulla macchina target (verificato che ffmpeg e' nel PATH),
    senza riportare i fotogrammi intermedi al controller per poi rimandarli indietro, risparmiando traffico e tempo.
  - Nessun ambiente personale cablato nel codice: macchina target / porta / cartelle provengono tutte dalla configurazione del Target.
  - Errore in un singolo segmento: se ne registra l'error, il task passa a failed, ma i segmenti gia' completati restano e si puo' riprendere dopo la riparazione.
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

_TASK_DIR = os.path.expanduser("~/.model-deploy-assistant/long_video")

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
                for s in job.get("shots", []):
                    if s.get("state") == "running":
                        s["state"] = "interrupted"
            _JOBS[job["job_id"]] = job
        except Exception:
            continue


def get_job(job_id: str) -> Optional[dict]:
    return _JOBS.get(job_id)


def start_long_video(target_id: str, storyboard: dict,
                     ref_image_paths: Optional[List[str]] = None,
                     width: int = 832, height: int = 480,
                     steps: int = 8, cfg: float = 1.0,
                     fps: int = 16) -> Dict[str, Any]:
    """Invia un task di generazione di video lungo, restituisce subito job_id, il thread in background avanza in serie.

    ref_image_paths: elenco dei percorsi assoluti delle immagini di riferimento dei personaggi sul controller (macchina locale). Ogni segmento usa
    R2V (MiniMaxH3ReferenceToVideo), con lo stesso gruppo di immagini di riferimento si blocca l'identita' dei personaggi, tra le inquadrature
    si usano normali tagli netti: non piu' il raccordo a catena «ultimo fotogramma del segmento precedente come primo del successivo» (l'ultimo fotogramma e' gia' derivato, a ogni segmento
    l'accumulo fa somigliare sempre meno il personaggio). Le immagini di riferimento si caricano una sola volta e tutti i segmenti le riusano.
    """
    ref_image_paths = ref_image_paths or []
    shots = storyboard.get("shots") or []
    if not shots:
        return {"success": False, "message": "Storyboard vuoto"}
    target = get_target(target_id)
    if not target:
        return {"success": False, "message": "Macchina target inesistente"}

    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "target_id": target_id,
        "title": storyboard.get("title", ""),
        "status": "running",
        "width": width, "height": height,
        "steps": steps, "cfg": cfg, "fps": fps,
        "ref_image_paths": ref_image_paths,
        "shots": [
            {"index": s["index"], "title": s.get("title", ""),
             "prompt": s["prompt"], "length": s["length"],
             "state": "pending", "prompt_id": "", "output_file": "",
             "error": ""}
            for s in shots
        ],
        "final_file": "",
        "error": "",
        "created_at": time.time(),
    }
    with _LOCK:
        _JOBS[job_id] = job
    _save(job)

    t = threading.Thread(target=_run_pipeline, args=(job_id,), daemon=True)
    t.start()
    return {"success": True, "job_id": job_id, "shot_count": len(job["shots"])}


def _comfy_input_dir(target) -> str:
    """Cartella input di ComfyUI sulla macchina target."""
    base = target.engine_path or ""
    return path_join(target, base, "input")


def _comfy_output_dir(target) -> str:
    base = target.engine_path or ""
    return path_join(target, base, "output", "modeldeploy")


def _run_pipeline(job_id: str):
    """Logica principale del thread in background: generazione segmento per segmento in serie -> estrazione dell'ultimo fotogramma per il raccordo -> concatenazione."""
    job = _JOBS.get(job_id)
    if not job:
        return
    target = get_target(job["target_id"])
    if not target:
        job["status"] = "failed"; job["error"] = "Macchina target inesistente"; _save(job); return

    executor = make_executor(target)
    try:
        engine = get_adapter(executor, target)
        if not hasattr(engine, "submit_workflow"):
            job["status"] = "failed"; job["error"] = "Il motore non supporta la generazione video"; _save(job); return
        if not engine.is_running():
            job["status"] = "failed"; job["error"] = "ComfyUI non e' in esecuzione, avviarlo prima"; _save(job); return

        input_dir = _comfy_input_dir(target)
        # R2V: le immagini di riferimento si caricano una sola volta, tutti i segmenti riusano lo stesso gruppo, l'identita' e' allineata internamente dal modello.
        ref_image_names = _upload_refs(executor, target, job, input_dir)
        if not ref_image_names:
            job["status"] = "failed"
            job["error"] = "Caricamento delle immagini di riferimento dei personaggi non riuscito, R2V non puo' bloccare l'identita'"
            _save(job)
            return

        for shot in job["shots"]:
            if shot["state"] == "completed":
                continue  # ripresa dal punto interrotto: i segmenti gia' completati vengono saltati. In R2V ogni segmento porta in modo indipendente le immagini di riferimento, non serve il raccordo con l'ultimo fotogramma
            ok = _generate_one_shot(engine, executor, target, job, shot, ref_image_names, input_dir)
            # read_file_slice failed di aimdo e' un bug intermittente (con gli stessi parametri il segmento nel giro precedente era riuscito),
            # per il segmento fallito si fanno al massimo 3 tentativi per assorbire i casi occasionali; solo se fallisce ancora l'intero segmento e' dichiarato fallito.
            attempt = 1
            while not ok and attempt < 3:
                attempt += 1
                shot["error"] = ""
                _save(job)
                time.sleep(3)
                ok = _generate_one_shot(engine, executor, target, job, shot, ref_image_names, input_dir)
            if not ok:
                job["status"] = "failed"
                job["error"] = f"Generazione del segmento {shot['index']} non riuscita: {shot['error']}"
                _save(job)
                return
            # In R2V tra le inquadrature si usano normali tagli netti, non si estrae piu' l'ultimo fotogramma come primo del segmento successivo (l'ultimo fotogramma e' gia' derivato, a catena si accumulerebbe)

        # Tutti i segmenti completati -> concatenazione
        final = _concat_shots(executor, target, job, input_dir)
        if final:
            job["final_file"] = final
            job["status"] = "completed"
        else:
            job["status"] = "failed"
            job["error"] = "Concatenazione del video finale non riuscita"
        _save(job)
    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"Eccezione nel task: {e}"
        _save(job)
    finally:
        executor.close()


def _upload_refs(executor, target, job, input_dir) -> List[str]:
    """Carica le immagini di riferimento dei personaggi del controller in ComfyUI/input della macchina target, restituisce l'elenco dei nomi file (una sola volta,
    tutti i segmenti le riusano). R2V blocca l'identita' dei personaggi con lo stesso gruppo di immagini di riferimento, tagli netti tra le inquadrature. Se il caricamento di una qualsiasi immagine fallisce
    restituisce l'elenco di quelle riuscite; se falliscono tutte restituisce una lista vuota (il chiamante ne deduce il fallimento del task)."""
    paths = job.get("ref_image_paths") or []
    names: List[str] = []
    for i, p in enumerate(paths):
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                data = f.read()
        except Exception:
            continue
        ext = os.path.splitext(p)[1] or ".png"
        name = f"mdref_{job['job_id']}_{i}{ext}"
        remote = path_join(target, input_dir, name)
        if executor.write_file_bytes(data, remote):
            names.append(name)
    return names


def _files_from_outputs(outputs: Dict[str, Any]) -> List[dict]:
    """Analizza gli outputs della history di ComfyUI ({node_id:{videos/gifs/images:[...]}})
    in un elenco piatto di file. engine.get_progress restituisce outputs, non i files del livello di routing."""
    files: List[dict] = []
    for node_out in (outputs or {}).values():
        for key in ("gifs", "videos", "images"):
            for f in node_out.get(key, []) or []:
                files.append({
                    "filename": f.get("filename", ""),
                    "subfolder": f.get("subfolder", ""),
                    "type": f.get("type", "output"),
                })
    return files


def _restart_comfyui(engine, timeout=150) -> bool:
    """Prima di ogni segmento riavvia ComfyUI da pulito.

    Il read_file_slice failed di comfy_aimdo a monte si presenta sempre al caricamento a freddo dopo che il modello e' stato scaricato dalla VRAM,
    e con molti segmenti / molti fotogrammi crolla persino il campionatore che legge i pesi UNet (misurato: con 9 segmenti x 103 fotogrammi al segmento 2 si blocca,
    nodo SamplerCustomAdvanced). --disable-smart-memory puo' solo ritardare, non risolve alla radice.
    L'unica via affidabile: garantire che ogni segmento sia il primo task di un processo pulito: prima della generazione stop -> start -> attesa di disponibilita'.
    Il costo e' che ogni segmento rifa' un caricamento a freddo del modello (circa 30-60s).
    """
    from ..services.engine_adapter import StartParams
    try:
        engine.stop()
    except Exception:
        pass
    time.sleep(8)  # attende il rilascio della porta
    try:
        engine.start(StartParams(model_path="", extra_args=[]))
    except Exception:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(6)
        try:
            if engine.is_running():
                return True
        except Exception:
            pass
    return False


def _generate_one_shot(engine, executor, target, job, shot,
                       ref_image_names, input_dir) -> bool:
    """Genera un singolo segmento (R2V): riavvio pulito di ComfyUI -> invio del workflow con immagini di riferimento -> polling -> registrazione dei prodotti.

    Ogni segmento usa lo stesso gruppo ref_image_names in R2V, l'identita' e' allineata internamente dal modello, tagli netti tra le inquadrature.
    """
    shot["state"] = "running"
    _save(job)
    try:
        if not _restart_comfyui(engine):
            shot["state"] = "failed"; shot["error"] = "Riavvio di ComfyUI: timeout, non e' pronto"
            _save(job); return False
        workflow = engine.build_video_workflow(
            prompt=shot["prompt"],
            model_name="",
            width=job["width"], height=job["height"],
            length=shot["length"],
            steps=job["steps"], cfg=job["cfg"],
            fps=job["fps"],
            ref_image_names=ref_image_names,
        )
        ok, result = engine.submit_workflow(workflow)
        if not ok:
            shot["state"] = "failed"; shot["error"] = str(result); _save(job); return False
        shot["prompt_id"] = result
        _save(job)

        # Polling fino al completamento (limite per segmento ~6 minuti)
        deadline = time.time() + 360
        while time.time() < deadline:
            time.sleep(8)
            prog = engine.get_progress(result)
            st = prog.get("state")
            if st == "completed":
                files = _files_from_outputs(prog.get("outputs"))
                mp4 = [f for f in files if f["filename"].lower().endswith((".mp4", ".webm", ".gif"))]
                if mp4:
                    shot["output_file"] = mp4[0]["filename"]
                    shot["state"] = "completed"
                    _save(job)
                    return True
                shot["state"] = "failed"; shot["error"] = "Completato ma senza file prodotti"; _save(job); return False
            if st == "error":
                shot["state"] = "failed"; shot["error"] = "Errore di esecuzione di ComfyUI"; _save(job); return False
        shot["state"] = "failed"; shot["error"] = "Timeout di generazione"; _save(job); return False
    except Exception as e:
        shot["state"] = "failed"; shot["error"] = str(e); _save(job); return False


def _extract_last_frame(executor, target, output_file, input_dir) -> str:
    """Sulla macchina target con ffmpeg estrae l'ultimo fotogramma di un segmento in input, restituisce il nome del file immagine (come primo fotogramma del segmento successivo). In caso di errore restituisce una stringa vuota."""
    if not output_file:
        return ""
    out_mp4 = path_join(target, _comfy_output_dir(target), output_file)
    frame_name = f"mdframe_{uuid.uuid4().hex[:8]}.png"
    frame_path = path_join(target, input_dir, frame_name)
    # -sseof -0.1 posiziona a 0.1s dalla fine, -update 1 -vframes 1 produce un solo fotogramma
    cmd = (f'ffmpeg -y -sseof -0.1 -i "{out_mp4}" -update 1 -vframes 1 '
           f'"{frame_path}"')
    r = executor.run(cmd, timeout=60)
    if r.ok:
        return frame_name
    return ""


def _concat_shots(executor, target, job, input_dir) -> str:
    """ffmpeg concat sulla macchina target di tutti i segmenti in final.mp4, restituisce il nome del file. In caso di errore restituisce una stringa vuota."""
    out_dir = _comfy_output_dir(target)
    files = [s["output_file"] for s in job["shots"] if s.get("output_file")]
    if not files:
        return ""
    # Scrive il file di lista concat (ogni riga file 'percorso assoluto')
    list_lines = "\n".join(f"file '{path_join(target, out_dir, f)}'" for f in files)
    list_name = f"mdconcat_{job['job_id']}.txt"
    list_path = path_join(target, out_dir, list_name)
    if not executor.write_file_bytes(list_lines.encode("utf-8"), list_path):
        return ""
    final_name = f"mdfinal_{job['job_id']}.mp4"
    final_path = path_join(target, out_dir, final_name)
    # -c copy richiede parametri di codifica identici per tutti i segmenti (con la stessa configurazione H3 e' soddisfatto)
    cmd = (f'ffmpeg -y -f concat -safe 0 -i "{list_path}" -c copy "{final_path}"')
    r = executor.run(cmd, timeout=120)
    if r.ok:
        return final_name
    # Se copy fallisce ripiega sulla ricodifica
    cmd2 = (f'ffmpeg -y -f concat -safe 0 -i "{list_path}" '
            f'-c:v libx264 -pix_fmt yuv420p "{final_path}"')
    r2 = executor.run(cmd2, timeout=240)
    return final_name if r2.ok else ""
