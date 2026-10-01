"""API di gestione del deploy (basata sul Target configurato dall'utente)"""

import os
import json
import shlex

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from typing import Optional

from ..models.target import get_target, upsert_target
from ..services.executor import make_executor
from ..services.engine_adapter import StartParams
from ..services.engine_registry import get_adapter
from ..services.collectors import path_join, detect_hardware
from ..services import tune_history

router = APIRouter()


class DeployRequest(BaseModel):
    target_id: str
    model: str
    # Testo dei parametri da riga di comando modificato a mano dall'utente (ha priorita'); es. "--ctx-size 8192 --batch-size 4096"
    args_text: Optional[str] = None
    # Retrocompatibilita': passaggio diretto della lista di parametri
    extra_args: Optional[list[str]] = None


def _adapter(target_id: str):
    target = get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Macchina target inesistente, configurarla prima nelle Impostazioni")
    executor = make_executor(target)
    return target, executor, get_adapter(executor, target)


# ==================== Registro dei modelli in esecuzione ====================
# Registra il nome del modello attualmente in esecuzione per ogni target, cosi' il frontend lo mantiene selezionato dopo un refresh (senza tornare al default).
# Persistito in un JSON locale: non si perde al riavvio del backend.
_RUNNING_FILE = os.path.expanduser("~/.model-deploy-assistant/running_models.json")


def _load_running() -> dict:
    try:
        with open(_RUNNING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {}


def _save_running(data: dict):
    os.makedirs(os.path.dirname(_RUNNING_FILE), exist_ok=True)
    with open(_RUNNING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _record_running(target_id: str, model: str):
    data = _load_running()
    data[target_id] = model
    _save_running(data)


# [2026-10-02 v1.1.29] Parametri con cui e' stato avviato il modello in esecuzione (per mostrare nel Monitoraggio se usa
# decodifica speculativa MTP / ngram). File separato: running_models.json resta {target: modello} (retrocompatibile).
# [2026-10-02 v1.1.32] implementazione spostata in services/running_args.py (condivisa con il tuner, che la scrive a ogni prova).
# Versione precedente: _ARGS_FILE + _record_args definiti qui.
from ..services import running_args as _running_args


def _record_args(target_id: str, args: list):
    _running_args.record(target_id, args)


def _clear_running(target_id: str):
    data = _load_running()
    if target_id in data:
        del data[target_id]
        _save_running(data)


def _get_running(target_id: str) -> str:
    return _load_running().get(target_id, "")


@router.get("/log")
def engine_log(target_id: str, lines: int = 40):
    """[2026-10-01 v1.1.12] Ultime righe del log di llama-server (Windows: C:\\temp\\llama_server.log, Linux: /tmp/llama_server.log):
    permette di vedere cosa fa il motore ora che la finestra della shell e' nascosta.

    [2026-10-01 v1.1.13] In piu' analizza il log per capire se la GPU e' usata davvero:
      - "offloaded N/M layers to GPU" -> offload {done, total} (N=0: il modello gira tutto su CPU)
      - dispositivi GPU citati (Vulkan0, ROCm0, CUDA0...) -> gpu_devices
    """
    import re
    target, executor, _ = _adapter(target_id)
    try:
        from ..services.tuner import _log_server_tail
        tail = _log_server_tail(executor, target, 300)
        offload = None
        for ln in tail:
            m = re.search(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers\s+to\s+GPU", ln, re.I)
            if m:
                offload = {"done": int(m.group(1)), "total": int(m.group(2))}
        gpu_devices = [ln.strip() for ln in tail if re.search(r"(Vulkan|ROCm|HIP|CUDA|Metal)\d*\s*:", ln)][:4]
        return {"lines": tail[-max(1, min(lines, 200)):], "offload": offload, "gpu_devices": gpu_devices}
    finally:
        executor.close()


@router.get("/models")
def list_models(target_id: str):
    """Elenca i file .gguf nella cartella dei modelli della macchina target (anche nelle sottocartelle)"""
    target, executor, _ = _adapter(target_id)
    try:
        # vLLM / SGLang caricano pesi HuggingFace (non GGUF): la scansione della cartella modelli non si applica,
        # si restituisce un avviso esplicito e l'utente inserisce nella pagina Deploy l'ID del modello o la cartella dei pesi locali.
        engine_type = getattr(target, "engine_type", "llama_cpp") or "llama_cpp"
        if engine_type in ("vllm", "sglang"):
            return {
                "models": [], "count": 0,
                "error": f"{engine_type} carica pesi HuggingFace (non GGUF): inserire direttamente l'ID del modello o la cartella dei pesi locali",
            }
        if not target.models_dir:
            return {"models": [], "count": 0, "error": "Cartella dei modelli non configurata"}
        # [2026-10-01 v1.1.0] Scansione RICORSIVA: .gguf anche nelle sottocartelle.
        # I nomi restituiti sono percorsi relativi a models_dir (es. "qwen/x.gguf").
        # Versione precedente (sostituita, leggeva solo il primo livello):
        # if target.os == "windows":
        #     pattern = f'{target.models_dir}\\*.gguf'
        #     result = executor.run(f'dir /b "{pattern}"', timeout=10)
        # else:
        #     result = executor.run(f'ls -1 "{target.models_dir}"/*.gguf 2>/dev/null', timeout=10)
        # models = []
        # if result.ok and result.stdout:
        #     for line in result.stdout.splitlines():
        #         line = line.strip()
        #         if line:
        #             models.append(line.split("\\")[-1].split("/")[-1])
        from ..services.model_scanner import scan_models
        models = scan_models(executor, target)
        return {"models": sorted(models), "count": len(models)}
    finally:
        executor.close()


@router.get("/video-models")
def list_video_models(target_id: str):
    """Scansiona la cartella diffusion_models di ComfyUI sulla macchina target ed elenca i modelli video realmente presenti.

    Come per la scansione dei .gguf nel deploy testuale: elenca solo i pesi effettivamente scaricati sulla macchina, senza piu' un elenco fisso nel codice.
    Per convenzione ComfyUI la sottocartella diffusion_models contiene i modelli di diffusione principali (UNet/DiT).
    """
    target = get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Macchina target inesistente")
    if not target.models_dir:
        return {"models": [], "count": 0, "error": "Cartella dei modelli non configurata"}

    diff_dir = path_join(target, target.models_dir, "diffusion_models")
    executor = make_executor(target)
    try:
        if target.os == "windows":
            pattern = f'{diff_dir}\\*.safetensors'
            result = executor.run(f'dir /b "{pattern}"', timeout=10)
        else:
            result = executor.run(f'ls -1 "{diff_dir}"/*.safetensors 2>/dev/null', timeout=10)
        models = []
        if result.ok and result.stdout:
            for line in result.stdout.splitlines():
                fn = line.strip().split("\\")[-1].split("/")[-1]
                # Filtra i file segnaposto di ComfyUI (put_xxx_here) e i nomi vuoti
                if not fn or fn.lower().startswith("put_"):
                    continue
                models.append({
                    "filename": fn,
                    "name": _pretty_video_name(fn),
                })
        return {"models": sorted(models, key=lambda m: m["filename"]), "count": len(models)}
    finally:
        executor.close()


def _pretty_video_name(filename: str) -> str:
    """Trasforma il nome del file dei pesi in un'etichetta leggibile (senza dipendere da alcun ambiente personale cablato nel codice)."""
    low = filename.lower()
    if "minimax" in low and "h3" in low:
        tag = "pruned int8" if "pruned" in low and "int8" in low else (
            "fp8" if "fp8" in low else ("bf16" if "bf16" in low else "int8"))
        return f"MiniMax H3 · {tag}"
    if "wan" in low:
        return "Wan 2.1"
    if "ltx" in low:
        return "LTX-Video"
    if "cogvideo" in low:
        return "CogVideoX"
    return filename.rsplit(".", 1)[0]


# Radice output di ComfyUI: preferisce engine_path/output, in alternativa output accanto a models_dir
def _comfy_output_root(target) -> str:
    base = target.engine_path or (target.models_dir or "").rstrip("\\/").rsplit("\\/", 1)[0].rsplit("/", 1)[0]
    if not base:
        return ""
    return path_join(target, base, "output")


def _safe_join(target, root: str, *parts: str) -> Optional[str]:
    """Concatena in modo sicuro dei segmenti di percorso sotto root, rifiutando l'uscita dalla cartella (con .. o percorsi assoluti)."""
    p = root
    for seg in parts:
        if seg is None:
            continue
        s = seg.strip()
        if not s:
            continue
        if ".." in s or s.startswith("/") or s.startswith("\\") or ":" in s:
            return None
        p = path_join(target, p, s)
    return p


@router.get("/video-file")
def get_video_file(target_id: str, filename: str, subfolder: str = ""):
    """Legge via SSH/locale il video finito in ComfyUI output sulla macchina target e lo restituisce come flusso di byte,
    per l'anteprima diretta con <video> nel frontend (proxy quando il controller non raggiunge direttamente la porta ComfyUI della macchina target)."""
    target = get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Macchina target inesistente")
    root = _comfy_output_root(target)
    if not root:
        raise HTTPException(status_code=400, detail="Cartella ComfyUI non configurata")
    full = _safe_join(target, root, subfolder, filename)
    if not full:
        raise HTTPException(status_code=400, detail="Percorso file non valido")

    executor = make_executor(target)
    try:
        data = executor.read_file_bytes(full)
    finally:
        executor.close()
    if data is None:
        raise HTTPException(status_code=404, detail="File video inesistente o lettura non riuscita")

    low = filename.lower()
    media = "video/webm" if low.endswith(".webm") else "video/mp4"
    return Response(content=data, media_type=media,
                    headers={"Cache-Control": "no-store"})


@router.post("/start")
def start_model(req: DeployRequest):
    """Avvia il modello (supporta i parametri di esecuzione inseriti a mano dall'utente)"""
    target, executor, engine = _adapter(req.target_id)
    try:
        model_path = path_join(target, target.models_dir, req.model)
        # Interpretazione dei parametri: args_text ha priorita', in alternativa extra_args
        if req.args_text and req.args_text.strip():
            try:
                extra = shlex.split(req.args_text.strip())
            except ValueError as e:
                return {"success": False, "message": f"Formato dei parametri errato: {e}"}
        else:
            extra = list(req.extra_args or [])
        # Il monitoraggio dipende dalle metriche; il servizio deve essere raggiungibile dall'esterno (host). Si aggiunge se manca, si elimina se duplicato
        joined = " ".join(extra)
        if "--metrics" not in joined:
            extra.append("--metrics")
        if "--host" not in joined:
            extra += ["--host", "0.0.0.0"]
        # Se l'utente cambia la porta nei parametri, la riscrive nella configurazione della macchina e la persiste, cosi' monitoraggio/stato/generazione la seguono automaticamente.
        # (--port e' gia' in extra: llama_cpp, se lo rileva, non aggiunge piu' il default, quindi la porta di avvio coincide)
        for i, tok in enumerate(extra):
            if tok == "--port" and i + 1 < len(extra):
                try:
                    pv = int(extra[i + 1])
                    if pv and pv != target.service_port:
                        target.service_port = pv
                        upsert_target(target)
                except ValueError:
                    pass
                break
        params = StartParams(model_path=model_path, extra_args=extra)
        success, msg = engine.start(params)
        if success:
            _record_running(req.target_id, req.model)
            _record_args(req.target_id, extra)
        return {"success": success, "message": msg, "args": extra, "port": target.service_port}
    finally:
        executor.close()


@router.post("/stop")
def stop_model(target_id: str):
    """Ferma il modello"""
    target, executor, engine = _adapter(target_id)
    try:
        success, msg = engine.stop()
        if success:
            _clear_running(target_id)
        return {"success": success, "message": msg}
    finally:
        executor.close()


@router.get("/spec")
def spec_info(target_id: str):
    """[2026-10-02 v1.1.29] Decodifica speculativa del modello in esecuzione: tipo (none / draft-mtp / ngram-*), n-max e dove vive.
    MTP = teste di predizione incluse nel modello (stessa memoria dei pesi: VRAM con n-gpu-layers all, RAM solo se gli strati
    del draft sono su CPU, vedi --spec-draft-ngl / --gpu-layers-draft); ngram-* = ricerca nella cronologia dei token (RAM/CPU, nessun
    modello aggiuntivo e nessun uso del disco)."""
    args = _running_args.get(target_id)
    if args is None:
        return {"known": False}
    def val(name, default=""):
        for i, tok in enumerate(args):
            if tok == f"--{name}" and i + 1 < len(args):
                return args[i + 1]
        return default
    stype = val("spec-type", "none")
    return {"known": True, "type": stype, "n_max": val("spec-draft-n-max"), "n_min": val("spec-draft-n-min"),
            "ngl": val("n-gpu-layers", val("gpu-layers")), "draft_ngl": val("spec-draft-ngl", val("gpu-layers-draft")),
            "kv_k": val("cache-type-k", "f16"), "kv_v": val("cache-type-v", "f16")}


@router.get("/status")
def get_status(target_id: str):
    """Restituisce lo stato di esecuzione. Il campo model riporta il nome del modello attualmente in esecuzione (se presente),
    cosi' il frontend dopo un refresh mantiene selezionato il modello in esecuzione invece di tornare al primo della lista."""
    target, executor, engine = _adapter(target_id)
    try:
        running = engine.is_running()
        model = _get_running(target_id) if running else ""
        return {"running": running, "engine": engine.name(), "model": model}
    finally:
        executor.close()


# ==================== Generazione video (ComfyUI) ====================

class VideoGenerateRequest(BaseModel):
    target_id: str
    prompt: str
    model_name: str
    negative_prompt: Optional[str] = ""
    width: int = 832
    height: int = 480
    length: int = 49
    steps: int = 30
    cfg: float = 6.0
    seed: Optional[int] = None
    fps: int = 16
    # Se far orchestrare al modello LLM la descrizione grezza in un prompt cinematografico e raccomandare i parametri di campionamento (percorso A)
    enhance: bool = False
    # I2V: percorso assoluto, sul controller (macchina locale), dell'immagine del primo fotogramma. Se non vuoto viene caricata su ComfyUI/input della macchina target
    # ed esegue image-to-video; se vuoto, T2V puro.
    image_path: Optional[str] = None
    # R2V: elenco dei percorsi assoluti, sul controller, di piu' immagini di riferimento dei personaggi. Se non vuoto si usa il reference-to-video
    # (MiniMaxH3ReferenceToVideo, l'identita' e' allineata internamente dal modello, blocca la coerenza dei personaggi).
    # Ha priorita' piu' alta di image_path.
    ref_image_paths: Optional[list] = None
    # Accelerazione di campionamento TeaCache: salta i passi di denoising adiacenti ridondanti. Misurato circa 1.3-3x di velocita' (secondo i passi).
    teacache: bool = False
    teacache_thresh: float = 0.15


@router.post("/generate")
def generate_video(req: VideoGenerateRequest):
    """Invia a ComfyUI un task di generazione text-to-video e restituisce prompt_id.

    Si applica solo ai Target con engine_type=comfyui. Dopo l'invio il frontend esegue il polling di
    /generate/progress per ottenere stato e percorso del video."""
    target, executor, engine = _adapter(req.target_id)
    try:
        if not hasattr(engine, "submit_workflow"):
            return {"success": False, "message": "Il motore della macchina target non supporta la generazione video, usare ComfyUI"}
        if not engine.is_running():
            return {"success": False, "message": "Il servizio ComfyUI non e' in esecuzione, avviarlo prima dalla pagina Deploy"}

        # Percorso A: orchestrazione facoltativa del prompt via LLM. Un fallimento degrada con grazia al prompt originale, senza mai bloccare la generazione.
        prompt = req.prompt
        steps = req.steps
        cfg = req.cfg
        enhanced = False
        reasoning = ""
        if req.enhance:
            from ..services.video_prompt import enhance_prompt
            # Deduce dall'input la modalita' di generazione H3: con immagine di riferimento R2V (sei sezioni, blocca l'identita'),
            # con immagine del primo fotogramma I2V (tre campi + istruzione di allineamento), altrimenti T2V.
            if req.ref_image_paths:
                _mode, _pc = "r2v", len(req.ref_image_paths)
            elif req.image_path:
                _mode, _pc = "i2v", 1
            else:
                _mode, _pc = "t2v", 0
            try:
                e = enhance_prompt(req.prompt, mode=_mode, picture_count=_pc)
            except Exception:
                e = None
            if e:
                prompt = e["prompt"]
                steps = e["steps"]
                cfg = e["cfg"]
                reasoning = e.get("reasoning", "")
                enhanced = True

        # I2V: carica l'immagine del primo fotogramma locale del controller su ComfyUI/input della macchina target e recupera il nome del file
        image_name = ""
        upload_err = ""
        if req.image_path:
            import os as _os
            import uuid as _uuid
            if not _os.path.exists(req.image_path):
                return {"success": False, "message": f"Immagine del primo fotogramma inesistente: {req.image_path}"}
            try:
                with open(req.image_path, "rb") as _f:
                    img_bytes = _f.read()
            except Exception as e:
                return {"success": False, "message": f"Lettura dell'immagine del primo fotogramma non riuscita: {e}"}
            ext = _os.path.splitext(req.image_path)[1] or ".png"
            image_name = f"mdframe_{_uuid.uuid4().hex[:8]}{ext}"
            input_dir = path_join(target, target.engine_path or "", "input")
            remote = path_join(target, input_dir, image_name)
            if not executor.write_file_bytes(img_bytes, remote):
                return {"success": False, "message": "Caricamento dell'immagine del primo fotogramma nella cartella input della macchina target non riuscito"}

        # R2V: carica su ComfyUI/input della macchina target le immagini di riferimento dei personaggi del controller e raccoglie l'elenco dei nomi file.
        # Ha priorita' su I2V (se la stessa richiesta fornisce entrambi si usa R2V).
        ref_image_names = []
        if req.ref_image_paths:
            import os as _os
            import uuid as _uuid
            input_dir = path_join(target, target.engine_path or "", "input")
            for rp in req.ref_image_paths:
                if not _os.path.exists(rp):
                    return {"success": False, "message": f"Immagine di riferimento inesistente: {rp}"}
                try:
                    with open(rp, "rb") as _f:
                        rb = _f.read()
                except Exception as e:
                    return {"success": False, "message": f"Lettura dell'immagine di riferimento non riuscita: {e}"}
                ext = _os.path.splitext(rp)[1] or ".png"
                rname = f"mdref_{_uuid.uuid4().hex[:8]}{ext}"
                rremote = path_join(target, input_dir, rname)
                if not executor.write_file_bytes(rb, rremote):
                    return {"success": False, "message": f"Caricamento dell'immagine di riferimento non riuscito: {rp}"}
                ref_image_names.append(rname)

        workflow = engine.build_video_workflow(
            prompt=prompt,
            model_name=req.model_name,
            negative_prompt=req.negative_prompt or "",
            width=req.width,
            height=req.height,
            length=req.length,
            steps=steps,
            cfg=cfg,
            seed=req.seed,
            fps=req.fps,
            image_name=image_name,
            ref_image_names=ref_image_names,
            teacache=req.teacache,
            teacache_thresh=req.teacache_thresh,
        )
        ok, result = engine.submit_workflow(workflow)
        if not ok:
            return {"success": False, "message": result}
        return {
            "success": True,
            "prompt_id": result,
            "message": "Task di generazione inviato",
            "enhanced": enhanced,
            "i2v": bool(image_name) and not ref_image_names,
            "r2v": bool(ref_image_names),
            "final_prompt": prompt,
            "final_steps": steps,
            "final_cfg": cfg,
            "reasoning": reasoning,
        }
    finally:
        executor.close()


@router.get("/generate/progress")
def generate_progress(target_id: str, prompt_id: str):
    """Interroga lo stato del task di generazione video; a completamento restituisce le informazioni sul video."""
    target, executor, engine = _adapter(target_id)
    try:
        if not hasattr(engine, "get_progress"):
            return {"state": "error", "message": "Il motore attuale non supporta l'interrogazione dei task di generazione"}
        prog = engine.get_progress(prompt_id)
        if prog.get("state") == "completed":
            outputs = prog.get("outputs") or {}
            files = []
            for node_out in outputs.values():
                for key in ("gifs", "videos", "images"):
                    for f in node_out.get(key, []) or []:
                        files.append({
                            "filename": f.get("filename", ""),
                            "subfolder": f.get("subfolder", ""),
                            "type": f.get("type", "output"),
                        })
            return {"state": "completed", "files": files}
        return {"state": prog.get("state", "unknown")}
    finally:
        executor.close()



# ==================== Parametri di default (riempimento da tuning / ripiego deterministico) ====================

# Ordine di visualizzazione della riga di comando (influisce sull'aspetto, non sulla funzionalita')
_ARG_ORDER = [
    "ctx-size", "n-gpu-layers", "batch-size", "ubatch-size",
    "cache-type-k", "cache-type-v", "flash-attn", "fit",
    "spec-type", "spec-draft-n-max", "spec-draft-n-min",
    "gpu-layers-draft", "spec-draft-ngl", "threads",
]


def _params_to_args_str(params: dict) -> str:
    """Dizionario piatto di parametri -> stringa di riga di comando modificabile"""
    keys = [k for k in _ARG_ORDER if k in params] + \
           [k for k in params if k not in _ARG_ORDER]
    parts = []
    for k in keys:
        v = params.get(k, "")
        if v == "" or v is None:
            parts.append(f"--{k}")
        else:
            parts.append(f"--{k} {v}")
    return " ".join(parts)


def _ensure_port(args_str: str, port: int) -> str:
    """Garantisce che la stringa di parametri contenga --port {port}: se c'e' gia' resta invariata (rispetta le modifiche dell'utente), altrimenti la aggiunge.
    Cosi' il riquadro parametri della pagina Deploy mostra la porta e l'utente puo' modificarla direttamente per fissare l'ascolto del servizio."""
    try:
        toks = args_str.split()
    except AttributeError:
        toks = []
    if "--port" in toks:
        return args_str
    return (args_str + f" --port {port}").strip()


def _model_size_gb(executor, target, model: str) -> float:
    """Interroga la dimensione reale (GB) del file del modello sulla macchina target; in caso di errore restituisce 0"""
    p = path_join(target, target.models_dir, model)
    if target.os == "windows":
        cmd = f'powershell -Command "(Get-Item \'{p}\').Length"'
    else:
        cmd = f'wc -c < "{p}"'
    r = executor.run(cmd, timeout=15)
    for tok in (r.stdout or "").split():
        if tok.isdigit():
            return round(int(tok) / (1024 ** 3), 2)
    return 0.0


@router.get("/default-args")
def default_args(target_id: str, model: str):
    """[2026-10-01 v1.1.11] Come _default_args_raw, ma toglie i parametri MTP (spec-type, spec-draft-*) se il modello o la
    build di llama-server non li supportano (altrimenti l'avvio dal Deploy poteva fallire con parametri non validi)."""
    res = _default_args_raw(target_id, model)
    target = get_target(target_id)
    if target and (getattr(target, "engine_type", "llama_cpp") or "llama_cpp") == "llama_cpp" and res.get("args") \
            and ("spec-" in res["args"] or "draft" in res["args"]) and target.engine_path:
        from ..services import tuner
        ex = make_executor(target)
        try:
            st = tuner.mtp_state(ex, target, model)
        finally:
            ex.close()
        if not st["allowed"]:
            toks, out, i = res["args"].split(), [], 0
            while i < len(toks):
                if toks[i].startswith("--") and tuner.is_spec_key(toks[i][2:]):
                    i += 2
                    continue
                out.append(toks[i]); i += 1
            res["args"] = " ".join(out)
            res.setdefault("reasoning", []).append("MTP non supportato da modello/build: parametri di decodifica speculativa rimossi")
    return res


def _default_args_raw(target_id: str, model: str):
    """Restituisce i parametri di deploy di default: priorita' al risultato di tuning piu' recente, altrimenti li calcola col generatore deterministico.

    Restituisce una stringa di riga di comando direttamente modificabile, per precompilare il riquadro parametri del frontend.
    """
    target = get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Macchina target inesistente")

    # 0) Motori diversi da llama.cpp (vLLM / SGLang): storico di tuning e generatore deterministico di parametri sono entrambi
    #    pensati solo per il sistema di parametri di llama.cpp e non si applicano a questi motori; restituisce direttamente i parametri di default generici dichiarati dall'adattatore del motore.
    engine_type = getattr(target, "engine_type", "llama_cpp") or "llama_cpp"
    if engine_type in ("vllm", "sglang"):
        from ..services import sglang, vllm
        mod = sglang if engine_type == "sglang" else vllm
        return {
            "args": _ensure_port(" ".join(getattr(mod, "DEFAULT_ARGS", [])), target.service_port),
            "source": "engine_default",
            "score": 0,
            "ts": "",
            "reasoning": [f"{engine_type} usa i parametri di default generici del motore (il motore non supporta ancora il tuning automatico)"],
        }

    # 1) Priorita': parametri dell'ultimo tuning
    rec = tune_history.get_latest(target_id, model)
    if rec and rec.get("params"):
        return {
            "args": _ensure_port(_params_to_args_str(rec["params"]), target.service_port),
            "source": rec.get("source", "tuner"),
            "score": rec.get("score", 0),
            "ts": rec.get("ts", ""),
        }

    # 2) Ripiego: generatore deterministico (richiede hardware rilevato al momento + dimensione del modello)
    executor = make_executor(target)
    try:
        from ..services.config_generator import generate_config
        hw = detect_hardware(executor, target)
        gpu = hw.get("gpu") or {}
        cpu = hw.get("cpu") or {}
        mem = hw.get("memory") or {}
        vram = gpu.get("total_memory_gb", 0) or 0
        if not vram and mem.get("total_gb"):
            # Memoria unificata Apple Silicon: VRAM utilizzabile stimata come memoria fisica x 0.75
            vram = round(mem["total_gb"] * 0.75, 1)
        size_gb = _model_size_gb(executor, target, model)
        cores = cpu.get("cores", 8) or 8
        threads = cpu.get("threads", 16) or 16
        gen = generate_config(
            gpu_vram_gb=vram, model_size_gb=size_gb, model_filename=model,
            ctx_size=8192, cpu_cores=cores, cpu_threads=threads,
        )
        return {
            "args": _ensure_port(_params_to_args_str(gen.get("params", {})), target.service_port),
            "source": "generated",
            "score": 0,
            "ts": "",
            "reasoning": gen.get("reasoning", []),
        }
    except Exception as e:
        # 3) Ultima risorsa: parametri vuoti, il backend usa i default del motore
        return {"args": _ensure_port("", target.service_port), "source": "default", "score": 0, "ts": "", "error": str(e)}
    finally:
        executor.close()



# ==================== Video lungo (storyboard -> I2V segmento per segmento -> concatenazione) ====================

class StoryboardRequest(BaseModel):
    theme: str
    total_seconds: int = 60
    max_shots: int = 12


@router.post("/storyboard")
def make_storyboard(req: StoryboardRequest):
    """Scompone il tema in uno storyboard (non salvato, restituisce solo l'anteprima per conferma/modifica dell'utente)."""
    from ..services.video_storyboard import generate_storyboard
    sb = generate_storyboard(req.theme, req.total_seconds, req.max_shots)
    if not sb:
        return {"success": False,
                "message": "Generazione dello storyboard non riuscita: verificare che nelle Impostazioni sia configurata un'API LLM utilizzabile"}
    return {"success": True, "storyboard": sb}


class LongVideoRequest(BaseModel):
    target_id: str
    storyboard: dict
    # R2V: elenco dei percorsi assoluti delle immagini di riferimento dei personaggi, ogni segmento condivide lo stesso gruppo per bloccare l'identita', tagli netti tra le inquadrature.
    ref_image_paths: Optional[list] = None
    width: int = 832
    height: int = 480
    steps: int = 8
    cfg: float = 1.0
    fps: int = 16


@router.post("/long-video")
def submit_long_video(req: LongVideoRequest):
    """Invia il task di generazione video lungo: segmento per segmento in R2V in serie (stesso gruppo di immagini di riferimento per bloccare l'identita' dei personaggi),
    tagli netti tra le inquadrature, concatenazione nel video finale. Restituisce subito job_id, il thread in background avanza; il frontend esegue il polling di
    /long-video/progress per l'avanzamento segmento per segmento."""
    from ..services.video_pipeline import start_long_video
    res = start_long_video(
        target_id=req.target_id,
        storyboard=req.storyboard,
        ref_image_paths=req.ref_image_paths or [],
        width=req.width, height=req.height,
        steps=req.steps, cfg=req.cfg, fps=req.fps,
    )
    return res


@router.get("/long-video/progress")
def long_video_progress(job_id: str):
    """Interroga l'avanzamento segmento per segmento del task di video lungo; a completamento restituisce il nome del file concatenato (per l'anteprima /video-file)."""
    from ..services.video_pipeline import get_job
    job = get_job(job_id)
    if not job:
        return {"status": "not_found"}
    return {
        "status": job["status"],
        "title": job.get("title", ""),
        "shots": [
            {"index": s["index"], "title": s.get("title", ""),
             "state": s["state"], "error": s.get("error", "")}
            for s in job["shots"]
        ],
        "final_file": job.get("final_file", ""),
        "target_id": job.get("target_id", ""),
        "error": job.get("error", ""),
    }



class UpscaleRequest(BaseModel):
    target_id: str
    # Alternativa: image_path e' il percorso assoluto dell'immagine locale del controller (verra' caricata nella cartella input della macchina target),
    # image_name e' il nome di un'immagine gia' presente nella cartella input di ComfyUI.
    image_path: Optional[str] = None
    image_name: Optional[str] = None
    out_w: int = 1920
    out_h: int = 1080


@router.post("/upscale")
def upscale_image(req: UpscaleRequest):
    """Invia il task di super-risoluzione AI di una singola immagine (RealESRGAN_x4plus -> lanczos fino a out_w x out_h).

    Riusa il motore ComfyUI; dopo l'invio si fa polling con /generate/progress (l'output SaveImage e' nel
    campo images, la logica di progress lo ha gia' raccolto). Disponibile solo per i Target con engine_type=comfyui."""
    target, executor, engine = _adapter(req.target_id)
    try:
        if not hasattr(engine, "build_upscale_workflow"):
            return {"success": False, "message": "Il motore attuale non supporta la super-risoluzione, usare ComfyUI"}
        if not engine.is_running():
            return {"success": False, "message": "Il servizio ComfyUI non e' in esecuzione, avviarlo prima dalla pagina Deploy"}
        name = req.image_name or ""
        if req.image_path:
            import os as _os
            import uuid as _uuid
            if not _os.path.exists(req.image_path):
                return {"success": False, "message": f"Immagine inesistente: {req.image_path}"}
            try:
                with open(req.image_path, "rb") as _f:
                    b = _f.read()
            except Exception as e:
                return {"success": False, "message": f"Lettura dell'immagine non riuscita: {e}"}
            ext = _os.path.splitext(req.image_path)[1] or ".png"
            name = f"mdup_{_uuid.uuid4().hex[:8]}{ext}"
            input_dir = path_join(target, target.engine_path or "", "input")
            if not executor.write_file_bytes(b, path_join(target, input_dir, name)):
                return {"success": False, "message": "Caricamento dell'immagine nella cartella input della macchina target non riuscito"}
        if not name:
            return {"success": False, "message": "Occorre fornire image_path o image_name"}
        wf = engine.build_upscale_workflow(
            image_name=name, out_w=req.out_w, out_h=req.out_h)
        ok, result = engine.submit_workflow(wf)
        if not ok:
            return {"success": False, "message": result}
        return {"success": True, "prompt_id": result, "message": "Task di super-risoluzione inviato"}
    finally:
        executor.close()


class UpscaleVideoRequest(BaseModel):
    target_id: str
    # Nome del file video finito in ComfyUI output (es. mdfinal_xxx.mp4)
    filename: str
    subfolder: str = "modeldeploy"
    out_w: int = 1920
    out_h: int = 1080
    fps: int = 16


@router.post("/upscale-video")
def submit_upscale_video(req: UpscaleVideoRequest):
    """Invia il task di super-risoluzione dell'intero video finito: estrazione fotogrammi -> super-risoluzione RealESRGAN fotogramma per fotogramma a out_w x out_h ->
    ricomposizione all'fps originale -> riapplicazione della traccia audio originale. Restituisce subito job_id, il thread in background avanza; il frontend esegue il polling di
    /upscale-video/progress per l'avanzamento fotogramma per fotogramma."""
    from ..services.upscale_pipeline import start_upscale_video
    return start_upscale_video(
        target_id=req.target_id,
        filename=req.filename,
        subfolder=req.subfolder,
        out_w=req.out_w, out_h=req.out_h, fps=req.fps,
    )


@router.get("/upscale-video/progress")
def upscale_video_progress(job_id: str):
    """Interroga l'avanzamento del task di super-risoluzione del video finito; a completamento restituisce il nome del file ad alta risoluzione (per l'anteprima /video-file)."""
    from ..services.upscale_pipeline import get_job
    job = get_job(job_id)
    if not job:
        return {"status": "not_found"}
    return {
        "status": job["status"],
        "total_frames": job.get("total_frames", 0),
        "done_frames": job.get("done_frames", 0),
        "final_file": job.get("final_file", ""),
        "target_id": job.get("target_id", ""),
        "error": job.get("error", ""),
    }