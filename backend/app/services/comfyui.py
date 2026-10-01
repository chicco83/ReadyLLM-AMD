"""Adattatore del motore ComfyUI (generazione di video / immagini)

Il paradigma di interazione di ComfyUI e' radicalmente diverso da llama.cpp / vLLM: non «carica un modello e poi continua a inferire
emettendo token», ma «servizio HTTP sempre attivo -> invio del JSON di un grafo di nodi workflow -> generazione asincrona in coda
-> polling di history per ottenere i file prodotti». Percio' questo adattatore, oltre a implementare il contratto base di EngineAdapter,
fornisce in piu' i metodi per inviare task di generazione e interrogare avanzamento / prodotti / VRAM.

Vincoli chiave:
  - ComfyUI ascolta in locale sulla macchina target (default 127.0.0.1:<port>), non raggiungibile dalla macchina locale (controller),
    quindi tutte le richieste HTTP devono partire dalla macchina target con curl (stesso schema con cui collectors raccoglie le metriche).
  - Il JSON del workflow puo' essere grande: si usa sempre write_file su disco + curl @file, aggirando il limite di lunghezza
    della riga di comando (limite di 8191 caratteri di Windows cmd, vedi l'esperienza gia' verificata di _curl_completion).
  - Nessun ambiente personale cablato nel codice: cartella di installazione / entry point python / porta provengono tutti dalla configurazione del Target.

Tutti i comandi operano sul Target configurato dall'utente.
"""

import base64
import json
import shlex
import time
import uuid
from typing import Optional

from .engine_adapter import EngineAdapter, StartParams
from .executor import Executor
from ..models.target import Target


def _path_join(target: Target, base: str, name: str) -> str:
    """Concatena i percorsi secondo il sistema operativo di destinazione (base e' la cartella, name il nome del file)."""
    sep = "\\" if target.os == "windows" else "/"
    return base.rstrip("\\/") + sep + name

# Porta di servizio predefinita di ComfyUI (ripiego quando Target.service_port non e' configurata esplicitamente)
DEFAULT_COMFY_PORT = 8188


def _comfy_port(target: Target) -> int:
    return target.service_port or DEFAULT_COMFY_PORT


class ComfyUIAdapter(EngineAdapter):
    def __init__(self, executor: Executor, target: Target):
        self.executor = executor
        self.target = target

    def name(self) -> str:
        return "comfyui"

    # ==================== Risoluzione di percorsi / comandi ====================

    def _comfy_dir(self) -> str:
        """Cartella radice di installazione di ComfyUI: proviene da engine_path (configurazione dell'utente), altrimenti ripiega su un default comune.
        Nota: qui non si cabla alcun percorso di macchine personali, il valore di ripiego e' solo una posizione convenzionale generica."""
        return self.target.engine_path or ""

    def _python_cmd(self) -> str:
        """Interprete python usato per l'avvio. Attenzione: engine_path e' la cartella radice di installazione di ComfyUI,
        non deve mai essere usato come interprete python (un bug storico faceva usare il nome della cartella come eseguibile nel comando di avvio,
        e il processo non partiva). Qui si ripiega sul python nel PATH; la versione portatile per Windows e' coperta da _start_windows,
        il cui bat rileva in linea la sottocartella python_embeded/python."""
        return "python"

    def _base_url(self) -> str:
        return f"http://127.0.0.1:{_comfy_port(self.target)}"

    # ==================== Rilevamento ====================

    def check_installed(self) -> bool:
        """Rileva se ComfyUI e' installato: il file di ingresso main.py esiste?"""
        d = self._comfy_dir()
        if not d:
            return False
        main_py = _path_join(self.target, d, "main.py")
        if self.target.os == "windows":
            result = self.executor.run(f'if exist "{main_py}" (echo FOUND)')
        else:
            result = self.executor.run(f'test -f "{main_py}" && echo FOUND')
        return "FOUND" in result.stdout

    # ==================== Avvio / Arresto ====================

    def start(self, params: StartParams) -> tuple:
        """Avvia in background il servizio ComfyUI. params.model_path nella semantica di ComfyUI non si usa
        (il modello e' indicato dal workflow), qui serve solo per la visualizzazione nel log."""
        port = _comfy_port(self.target)
        d = self._comfy_dir()
        if not d:
            return False, "Cartella di installazione di ComfyUI non configurata (engine_path deve puntare alla cartella radice di ComfyUI)"
        main_py = _path_join(self.target, d, "main.py")
        # --listen 0.0.0.0 facilita l'accesso con inoltro di porta dalla macchina locale; --port indica la porta
        # --disable-async-offload --disable-mmap: aggirano il read_file_slice failed del backend
        # di I/O asincrono dei pesi comfy_aimdo durante la lettura dei frammenti safetensors nella decodifica VAE (misurato:
        # si presenta sempre con RTX4090+H3; disattivandoli si ripiega sul caricamento normale, campionamento+decodifica passano tutti e il video viene prodotto).
        # --disable-smart-memory: disattiva la gestione intelligente della VRAM, dopo il caricamento i modelli non vengono scaricati proattivamente.
        # Fondamentale per gli scenari di video lungo a piu' segmenti: smart-memory scarica video_vae dalla VRAM tra un task e l'altro,
        # e al caricamento a freddo di vae.encode nel segmento I2V successivo si ricade in aimdo read_file_slice failed;
        # tenendolo residente senza scaricarlo si evita alla radice quel percorso di caricamento a freddo.
        run_args = (
            f'"{main_py}" --listen 0.0.0.0 --port {port} '
            '--disable-async-offload --disable-mmap --disable-smart-memory'
        )

        if self.target.os == "windows":
            return self._start_windows(d, run_args)
        return self._start_linux(d, run_args)

    def _start_windows(self, d: str, run_args: str) -> tuple:
        # Il bat rileva in linea python: prima la versione portatile python_embeded/python.exe,
        # poi python/python.exe, infine ripiega sul python nel PATH. Evita di usare come interprete il nome della cartella di ComfyUI
        # (bug storico) o che il processo non parta perche' nel PATH non c'e' python.
        bat_content = (
            '@echo off\r\n'
            f'cd /d "{d}"\r\n'
            'set "PY=python"\r\n'
            f'if exist "{d}\\python_embeded\\python.exe" set "PY={d}\\python_embeded\\python.exe"\r\n'
            f'if exist "{d}\\python\\python.exe" set "PY={d}\\python\\python.exe"\r\n'
            f'"%PY%" {run_args}\r\n'
        )
        b64 = base64.b64encode(bat_content.encode("gbk")).decode("ascii")
        bat_path = r"C:\temp\comfy_start.bat"
        write_cmd = (
            'powershell -Command "'
            "New-Item -Path C:\\temp -ItemType Directory -Force | Out-Null; "
            f"[IO.File]::WriteAllBytes('{bat_path}', [Convert]::FromBase64String('{b64}'))"
            '"'
        )
        result = self.executor.run(write_cmd, timeout=15)
        if not result.ok:
            return False, f"Scrittura dello script di avvio non riuscita: {result.stdout} {result.stderr}"
        run_cmd = (
            'schtasks /create /tn ComfyUI /tr "%s" /sc once /st 00:00 /f '
            '&& schtasks /run /tn ComfyUI' % bat_path
        )
        result = self.executor.run(run_cmd, timeout=15)
        if not result.ok:
            return False, f"Avvio non riuscito: {result.stdout} {result.stderr}"
        return True, "Comando di avvio di ComfyUI inviato (al primo avvio devono caricarsi le dipendenze, attendere con pazienza)"

    def _start_linux(self, d: str, run_args: str) -> tuple:
        py = self._python_cmd()
        cmd = f'cd "{d}" && nohup {py} {run_args} > /tmp/comfyui.log 2>&1 &'
        result = self.executor.run(cmd, timeout=20)
        if not result.ok:
            return False, f"Avvio non riuscito: {result.stdout} {result.stderr}"
        return True, "Comando di avvio di ComfyUI inviato"

    def stop(self) -> tuple:
        if self.target.os == "windows":
            self.executor.run('schtasks /end /tn ComfyUI', timeout=10)
            result = self.executor.run("taskkill /f /im python.exe", timeout=10)
            # taskkill python.exe e' troppo ampio, ma su Windows ComfyUI di solito e' proprio un processo python;
            # per essere piu' precisi servirebbe terminare per porta, qui si mantiene la stessa strategia «best effort» di llama
        else:
            result = self.executor.run("pkill -f 'ComfyUI/main.py'", timeout=10)
        if result.ok:
            return True, "Servizio ComfyUI fermato"
        return False, f"Esito dell'arresto: {result.stdout} {result.stderr}"

    def is_running(self) -> bool:
        # Controllo di salute: ComfyUI fornisce /system_stats, se e' raggiungibile si considera in esecuzione
        return self._curl_json("/system_stats", timeout=8) is not None

    def get_metrics_url(self) -> str:
        return f"{self._base_url()}/system_stats"

    # ==================== Task di generazione (specifici di ComfyUI) ====================

    def _remote_tmp(self, name: str) -> str:
        if self.target.os == "windows":
            return f"C:\\temp\\{name}"
        return f"/tmp/{name}"

    def _curl_json(self, path: str, timeout: int = 10) -> Optional[dict]:
        """Esegue curl su un endpoint GET sulla macchina target e interpreta il JSON; in caso di errore restituisce None."""
        url = f"{self._base_url()}{path}"
        if self.target.os == "windows":
            cmd = f'curl -s --max-time {timeout} "{url}"'
        else:
            cmd = f"curl -s --max-time {timeout} {shlex.quote(url)}"
        result = self.executor.run(cmd, timeout=timeout + 5)
        out = (result.stdout or "").strip()
        if not out or not out.startswith("{"):
            return None
        try:
            return json.loads(out)
        except (ValueError, json.JSONDecodeError):
            return None

    def submit_workflow(self, workflow: dict, client_id: Optional[str] = None) -> tuple:
        """Invia a /prompt il JSON di un grafo di nodi workflow, restituisce (ok, prompt_id o errore).

        Il workflow e' gia' nel formato prompt standard di ComfyUI (non il formato litegraph esportato dalla UI).
        I JSON grandi usano write_file su disco + curl @file, per evitare il troncamento della riga di comando."""
        cid = client_id or uuid.uuid4().hex
        payload = {"prompt": workflow, "client_id": cid}
        body = json.dumps(payload, ensure_ascii=False)
        tmp = self._remote_tmp(f"comfy_prompt_{cid}.json")
        if not self.executor.write_file(body, tmp):
            return False, "Scrittura del file temporaneo del workflow non riuscita"

        url = f"{self._base_url()}/prompt"
        if self.target.os == "windows":
            cmd = f'curl -s --max-time 30 -X POST "{url}" -H "Content-Type: application/json" --data "@{tmp}"'
        else:
            cmd = f"curl -s --max-time 30 -X POST {shlex.quote(url)} -H 'Content-Type: application/json' --data @{shlex.quote(tmp)}"
        result = self.executor.run(cmd, timeout=35)
        out = (result.stdout or "").strip()
        try:
            data = json.loads(out)
        except (ValueError, json.JSONDecodeError):
            return False, f"Invio non riuscito, risposta non interpretabile: {out[:300]}"
        if "prompt_id" in data:
            return True, data["prompt_id"]
        # Se la validazione di ComfyUI fallisce restituisce il dettaglio dell'errore
        err = data.get("error") or data
        return False, f"Workflow rifiutato: {json.dumps(err, ensure_ascii=False)[:400]}"

    def get_history(self, prompt_id: str) -> Optional[dict]:
        """Interroga la cronologia dei task; a task completato in outputs ci sono le informazioni sui file prodotti (video/immagini)."""
        return self._curl_json(f"/history/{prompt_id}", timeout=10)

    def get_queue(self) -> Optional[dict]:
        """Interroga la coda: running + pending, per stabilire se il task e' in esecuzione."""
        return self._curl_json("/queue", timeout=8)

    def get_progress(self, prompt_id: str) -> dict:
        """Restituisce lo stato a grana grossa del task (senza dipendere da WebSocket, solo polling):
        {state: queued|running|completed|unknown, ...}

        L'avanzamento passo-passo (step/total) di ComfyUI viene spinto solo via WebSocket, lato HTTP non e' ottenibile direttamente;
        P0 deduce lo stato da coda + history, la barra di avanzamento mostra i tre stati «in coda/in generazione/completato»."""
        hist = self.get_history(prompt_id)
        if hist and prompt_id in hist:
            entry = hist[prompt_id]
            status = entry.get("status", {}) or {}
            # Punto chiave: un record in history != successo. ComfyUI scrive in history anche quando l'esecuzione da' errore,
            # bisogna prima guardare status_str, altrimenti un segmento fallito viene scambiato per completed (con outputs vuoto).
            if status.get("status_str") == "error":
                msg = ""
                for m in status.get("messages", []) or []:
                    if isinstance(m, list) and len(m) > 1 and m[0] == "execution_error":
                        info = m[1] or {}
                        msg = "%s @node %s" % (
                            info.get("exception_message", "").strip(),
                            info.get("node_type", ""))
                        break
                return {"state": "error", "message": msg or "Errore di esecuzione di ComfyUI"}
            outputs = entry.get("outputs", {})
            return {"state": "completed", "outputs": outputs}
        q = self.get_queue()
        if q:
            for item in q.get("queue_running", []):
                # item[1] e' il prompt_id
                if len(item) > 1 and item[1] == prompt_id:
                    return {"state": "running"}
            for item in q.get("queue_pending", []):
                if len(item) > 1 and item[1] == prompt_id:
                    return {"state": "queued"}
        return {"state": "unknown"}

    def get_system_stats(self) -> Optional[dict]:
        """Informazioni su VRAM / dispositivi: /system_stats restituisce l'elenco devices con vram_total/vram_free."""
        return self._curl_json("/system_stats", timeout=8)

    # ==================== Aiuto per l'attesa dello stato di salute ====================

    def wait_ready(self, max_wait: int = 60) -> bool:
        """Fa polling finche' ComfyUI non risponde (utile quando il primo avvio, con il caricamento delle dipendenze, e' lento)."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            if self.is_running():
                return True
            time.sleep(2)
        return False


    # ==================== Template di workflow per la generazione video (MiniMax H3) ====================

    # Nomi di file dei pesi predefiniti di H3 (riconfezionamento Comfy-Org/MiniMax-H3, combinazione compressa al limite per 24G di VRAM)
    H3_UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    H3_CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    H3_VAE_VIDEO = "minimax_h3_video_vae_fp16.safetensors"
    H3_VAE_AUDIO = "minimax_h3_audio_vae_fp32.safetensors"
    H3_LORA_8STEP = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"

    def build_video_workflow(
        self,
        prompt: str,
        model_name: str = "",
        negative_prompt: str = "",
        width: int = 1280,
        height: int = 720,
        length: int = 49,
        steps: int = 8,
        cfg: float = 1.0,
        seed: Optional[int] = None,
        fps: int = 24,
        image_name: str = "",
        ref_image_names: Optional[list] = None,
        teacache: bool = False,
        teacache_thresh: float = 0.15,
    ) -> dict:
        """Costruisce il prompt API di ComfyUI (formato piatto) per text-to-video / image-to-video di MiniMax H3.

        Se image_name non e' vuoto si usa I2V: carica quell'immagine del primo fotogramma dalla cartella input di ComfyUI e la collega alla porta first_frame di
        MiniMaxH3ImageToVideo (il nodo esegue internamente vae.encode,
        non serve alcun nodo VAEEncode aggiuntivo). Se vuoto e' T2V puro.

        Il workflow T2V ufficiale incapsula la logica di generazione nei nuovi «sottografi (subgraph)» di ComfyUI,
        l'endpoint /prompt accetta solo il formato API piatto, quindi qui i 21 nodi interni del sottografo vengono appiattiti nel
        prompt API di primo livello, e i parametri esterni del sottografo (prompt/risoluzione/durata/seed/nome dei pesi)
        vengono iniettati nei nodi corrispondenti. La struttura si basa sull'analisi misurata di video_minimax_h3_t2v.json
        di Comfy-Org/workflow_templates (nodi 119-139 + SaveVideo).

        Parametri:
          prompt    prompt positivo (H3 non ha un nodo di prompt negativo indipendente, negative viene ignorato)
          duration  durata in secondi (H3 converte in numero di fotogrammi length con una formula, non e' direttamente il numero di fotogrammi)
          steps     passi di campionamento (con turbo lora si consiglia 8)
          seed      seme casuale (None = casuale)
        I nomi dei file dei pesi sono fissati sulla combinazione compressa al limite per 24G di VRAM (UNet pruned+int8_convrot,
        codificatore testuale nvfp4, doppio VAE, turbo lora a 8 step); per cambiare livello si modificano le costanti della classe.
        """
        sd = seed if seed is not None else int(uuid.uuid4().int % (2 ** 32 - 1))
        unet = model_name or self.H3_UNET
        wf = {
            "119": {"class_type": "VAELoader",
                    "inputs": {"vae_name": self.H3_VAE_VIDEO}},
            "120": {"class_type": "VAELoader",
                    "inputs": {"vae_name": self.H3_VAE_AUDIO}},
            "127": {"class_type": "UNETLoader",
                    "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "128": {"class_type": "CLIPLoader",
                    "inputs": {"clip_name": self.H3_CLIP, "type": "minimax", "device": "default"}},
            "134": {"class_type": "LoraLoaderModelOnly",
                    "inputs": {"model": ["127", 0], "lora_name": self.H3_LORA_8STEP,
                               "strength_model": 1.0}},
            # use_turbo=False -> lo switch va su on_false (UNet originale); True -> on_true (LoRA)
            "139": {"class_type": "PrimitiveBoolean", "inputs": {"value": True}},
            "135": {"class_type": "ComfySwitchNode",
                    "inputs": {"on_false": ["127", 0], "on_true": ["134", 0], "switch": ["139", 0]}},
            # Cambio degli steps: turbo usa 138 (steps iniettati), non turbo usa 137 (fissi a 20)
            "138": {"class_type": "PrimitiveInt", "inputs": {"value": steps}},
            "137": {"class_type": "PrimitiveInt", "inputs": {"value": 20}},
            "136": {"class_type": "ComfySwitchNode",
                    "inputs": {"on_false": ["137", 0], "on_true": ["138", 0], "switch": ["139", 0]}},
            "123": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "124": {"class_type": "BasicScheduler",
                    "inputs": {"model": ["135", 0], "scheduler": "simple",
                               "steps": ["136", 0], "denoise": 1.0}},
            "129": {"class_type": "RandomNoise", "inputs": {"noise_seed": sd}},
            # Numero di fotogrammi length -> numero di fotogrammi valido allineato a 17 per H3 (PrimitiveFloat=length -> MathExpression)
            "133": {"class_type": "PrimitiveFloat", "inputs": {"value": float(length)}},
            "132": {"class_type": "ComfyMathExpression",
                    "inputs": {"expression": "max(5, a) + (5 - (max(5, a) % 17)) % 17",
                               "values.a": ["133", 0]}},
            "131": {"class_type": "MiniMaxH3ImageToVideo",
                    "inputs": {"clip": ["128", 0], "vae": ["119", 0], "prompt": prompt,
                               "width": width, "height": height, "length": ["132", 1]}},
            "126": {"class_type": "BasicGuider",
                    "inputs": {"model": ["135", 0], "conditioning": ["131", 0]}},
            "125": {"class_type": "SamplerCustomAdvanced",
                    "inputs": {"noise": ["129", 0], "guider": ["126", 0], "sampler": ["123", 0],
                               "sigmas": ["124", 0], "latent_image": ["131", 1]}},
            "122": {"class_type": "VAEDecode",
                    "inputs": {"samples": ["125", 0], "vae": ["119", 0]}},
            "121": {"class_type": "VAEDecodeAudio",
                    "inputs": {"samples": ["125", 0], "vae": ["120", 0]}},
            "130": {"class_type": "CreateVideo",
                    "inputs": {"images": ["122", 0], "audio": ["121", 0], "fps": fps}},
            "92": {"class_type": "SaveVideo",
                   "inputs": {"video": ["130", 0], "filename_prefix": "modeldeploy/video",
                              "format": "auto"}},
        }
        # R2V: quando c'e' un'immagine di riferimento, sostituisce il nodo 131 con MiniMaxH3ReferenceToVideo, usando
        # le porte autogrow ref_image_N per collegare piu' LoadImage. L'identita' e' allineata internamente dal modello
        # (i token di riferimento attraversano ogni passo di campionamento), piu' affidabile del «pre-generare un fotogramma di ancoraggio per inquadratura»: il testo-a-immagine
        # produce volti incoerenti tra immagini diverse, mentre R2V blocca l'identita' direttamente con le immagini di riferimento. Il prompt le cita con <Picture i>.
        # Nota: R2V ha un ingresso audio_vae in piu' rispetto a I2V (il nodo 120 e' gia' presente nel workflow).
        if ref_image_names:
            wf["131"] = {"class_type": "MiniMaxH3ReferenceToVideo",
                         "inputs": {"clip": ["128", 0], "vae": ["119", 0],
                                    "audio_vae": ["120", 0], "prompt": prompt,
                                    "width": width, "height": height,
                                    "length": ["132", 1],
                                    "ref_image_size": "match"}}
            # ref_images e' un ingresso Autogrow, nel formato API va serializzato come dict annidato
            # {ref_image_0: [node,port], ref_image_1: ...}, non si puo' passare ref_image_0
            # come parametro di primo livello (altrimenti execute riceve l'argomento inatteso 'ref_image_0').
            ref_map = {}
            for i, name in enumerate(ref_image_names[:9]):
                nid = "15%d" % i  # 150,151,...
                wf[nid] = {"class_type": "LoadImage", "inputs": {"image": name}}
                ref_map["ref_image_%d" % i] = [nid, 0]
            wf["131"]["inputs"]["ref_images"] = ref_map
        elif image_name:
            # I2V: quando c'e' un'immagine del primo fotogramma si aggancia un nodo LoadImage, collegato a MiniMaxH3ImageToVideo.first_frame
            wf["140"] = {"class_type": "LoadImage",
                         "inputs": {"image": image_name}}
            wf["131"]["inputs"]["first_frame"] = ["140", 0]
        # TeaCache: tra il modello finale (135) e guider/scheduler si inserisce un nodo di cache, saltando i passi di denoising
        # adiacenti ridondanti. total_steps deve essere uguale al numero reale di passi di campionamento (steps), altrimenti la finestra della cache si sfasa.
        # start_step=2/end_step=-2: i primi 2 passi (che definiscono la struttura) e gli ultimi 2 (che definiscono i dettagli) vengono sempre calcolati realmente.
        if teacache:
            wf["145"] = {"class_type": "MiniMaxH3TeaCache",
                         "inputs": {"model": ["135", 0],
                                    "rel_l1_thresh": teacache_thresh,
                                    "start_step": 2, "end_step": -2,
                                    "total_steps": int(steps)}}
            wf["126"]["inputs"]["model"] = ["145", 0]
            wf["124"]["inputs"]["model"] = ["145", 0]
        return wf

    def build_upscale_workflow(
        self,
        image_name: str,
        out_w: int = 1920,
        out_h: int = 1080,
        model_name: str = "RealESRGAN_x4plus.pth",
        filename_prefix: str = "modeldeploy/upscaled",
    ) -> dict:
        """Costruisce il prompt API di ComfyUI (formato piatto) per la super-risoluzione di una singola immagine.

        Catena: LoadImage -> UpscaleModelLoader -> ImageUpscaleWithModel
        -> ImageScale (riduzione precisa a out_w x out_h) -> SaveImage.

        RealESRGAN_x4plus ingrandisce sempre 4x: 832x480 diventa prima 3328x1920, poi con
        ImageScale (lanczos/downscale) converge al target 1920x1080, evitando dimensioni fuori controllo.
        image_name deve essere un'immagine gia' presente nella cartella input di ComfyUI (immagine di riferimento / fotogramma estratto).
        """
        return {
            "200": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "201": {"class_type": "UpscaleModelLoader",
                    "inputs": {"model_name": model_name}},
            "202": {"class_type": "ImageUpscaleWithModel",
                    "inputs": {"upscale_model": ["201", 0], "image": ["200", 0]}},
            "203": {"class_type": "ImageScale",
                    "inputs": {"image": ["202", 0], "upscale_method": "lanczos",
                               "downscale_method": "lanczos",
                               "width": int(out_w), "height": int(out_h),
                               "crop": "disabled"}},
            "204": {"class_type": "SaveImage",
                    "inputs": {"images": ["203", 0], "filename_prefix": filename_prefix}},
        }
