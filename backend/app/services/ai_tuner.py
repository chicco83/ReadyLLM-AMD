"""Servizio di tuning con AI Agent

Modalita' Agent: chiama l'API del modello di grandi dimensioni configurata dall'utente e fa dedurre all'LLM, in base a hardware/modello/scenario,
i parametri ottimali, con supporto a piu' iterazioni (risultati di misura rimandati all'LLM -> seconda ottimizzazione -> nuova misura -> ...).

Flusso:
  1. Costruzione del system prompt (hardware, modello, whitelist dei parametri, requisiti di formato JSON)
  2. Chiamata all'LLM -> interpretazione di action: test (fornisce parametri) / done (raccomandazione finale)
  3. action=test -> avvia il modello e misura -> risultato aggiunto alla conversazione -> si torna al punto 2
  4. action=done -> produce raccomandazione finale + analisi
  5. Al raggiungimento del numero massimo di round si termina forzatamente

La configurazione e' persistita in ~/.model-deploy-assistant/ai_config.json
"""

import json
import os
import threading
import time
import uuid
import urllib.request
import urllib.error
from typing import Optional, List, Dict

from ..models.target import Target, get_target
from .executor import Executor
from .engine_adapter import StartParams
from .llama_cpp import LlamaCppAdapter
from .config_generator import generate_config

# ==================== Configurazione ====================

_CONFIG_DIR = os.path.expanduser("~/.model-deploy-assistant")
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "ai_config.json")

# Whitelist dei parametri: l'LLM puo' raccomandare solo questi
PARAM_WHITELIST = [
    "batch-size", "ubatch-size", "threads", "threads-batch",
    "n-gpu-layers", "gpu-layers", "gpu-layers-draft",
    "cache-type-k", "cache-type-v",
    "flash-attn", "spec-type", "spec-draft-n-max", "spec-draft-n-min",
    "spec-draft-ngl", "fit", "parallel", "numa", "mlock", "no-mmap",
    "rope-scaling", "keep", "tensor-split", "main-gpu", "split-mode",
    "override-kv", "load-mode",
]

MAX_ROUNDS = 8  # numero massimo di round di iterazione


def get_config() -> dict:
    """Legge la configurazione AI"""
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"api_url": "", "api_key": "", "model_name": ""}


def save_config(cfg: dict):
    """Salva la configurazione AI"""
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def test_connection(cfg: dict) -> dict:
    """Verifica la connettivita' dell'API LLM, restituisce {ok, message, model_info}"""
    url = cfg.get("api_url", "").rstrip("/")
    if not url:
        return {"ok": False, "message": "Indirizzo API vuoto"}
    # Prova l'endpoint /v1/models
    models_url = f"{url}/models" if "/v1" in url else f"{url}/v1/models"
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    try:
        req = urllib.request.Request(models_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            models = [m.get("id", "") for m in data.get("data", [])]
            return {"ok": True, "message": f"Connessione riuscita, modelli disponibili: {', '.join(models[:5])}",
                    "models": models}
    except urllib.error.HTTPError as e:
        # 401/403 indica indirizzo corretto ma problema di autenticazione; 404 puo' significare nessun endpoint /models ma servizio presente
        if e.code in (401, 403):
            return {"ok": False, "message": f"Autenticazione non riuscita (HTTP {e.code}), controllare la API Key"}
        return {"ok": True, "message": f"Servizio raggiungibile (HTTP {e.code}), ma impossibile elencare i modelli"}
    except Exception as e:
        return {"ok": False, "message": f"Connessione non riuscita: {e}"}


# ==================== Costruzione del prompt ====================

def _build_system_prompt(hardware: dict, model_info: dict, ctx_size: int,
                         goal: str, user_desc: str,
                         baseline_params: dict = None,
                         baseline_metrics: dict = None) -> str:
    """Costruisce il system prompt.
    Nuova architettura: il ruolo dell'LLM e' quello di «esperto di rifinitura», non di «indovinare i parametri da zero».
    Il generatore deterministico ha gia' fornito una configurazione di base verificata col calcolo, l'LLM esplora in piccolo a partire da essa.
    """
    hw_lines = []
    gpu = hardware.get("gpu", {})
    cpu = hardware.get("cpu", {})
    mem = hardware.get("memory", {})
    if gpu:
        hw_lines.append(f"- GPU: {gpu.get('name', 'sconosciuta')} {gpu.get('total_memory_gb', '?')}GB di VRAM")
    if cpu:
        hw_lines.append(f"- CPU: {cpu.get('name', 'sconosciuta')} {cpu.get('cores', '?')} core {cpu.get('threads', '?')} thread")
    if mem:
        hw_lines.append(f"- Memoria: {mem.get('total_gb', '?')}GB")
    hw_lines.append(f"- Sistema: {hardware.get('os', 'sconosciuto')}")

    mi_lines = [
        f"- File: {model_info.get('filename', 'sconosciuto')}",
        f"- Dimensione: {model_info.get('size_gb', '?')}GB",
    ]

    # Informazioni di baseline (output del generatore deterministico + risultati misurati)
    baseline_section = ""
    if baseline_params and baseline_metrics:
        baseline_section = f"""
## Configurazione di baseline verificata (il tuo punto di partenza)
I parametri seguenti sono stati generati da un algoritmo deterministico e verificati con misure reali, sono il miglior punto di partenza noto:

Parametri: {json.dumps(baseline_params, ensure_ascii=False)}

Risultati misurati:
- Velocita' di decodifica: {baseline_metrics.get('decode', '?')} t/s
- Velocita' di prefill: {baseline_metrics.get('prefill', '?')} t/s
- Utilizzo GPU: {baseline_metrics.get('gpu_util', '?')}%
- VRAM GPU: {baseline_metrics.get('gpu_mem_pct', '?')}%
- CPU: {baseline_metrics.get('cpu_pct', '?')}%

Il tuo compito e' fare piccole rifiniture su questa baseline, cercando di trovare una configurazione migliore.
Non allontanarti molto dalla baseline (ad es. passare ngl a scarico parziale, togliere la decodifica speculativa): queste sono gia' state verificate come direzione ottimale.
"""

    return f"""Sei un esperto di rifinitura dei parametri di inferenza di llama.cpp. Il sistema ha gia' generato con un algoritmo deterministico una configurazione di base verificata; il tuo compito e' esplorare in piccolo a partire da essa, cercando possibili miglioramenti di prestazioni.

## Ambiente hardware
{chr(10).join(hw_lines)}

## Informazioni sul modello
{chr(10).join(mi_lines)}

## Esigenze dell'utente
- Lunghezza di contesto: {ctx_size}
- Obiettivo di ottimizzazione: {goal}
- Descrizione dello scenario: {user_desc or 'non fornita'}
{baseline_section}
## Whitelist dei parametri disponibili
Puoi raccomandare solo i parametri seguenti (non inventare parametri inesistenti):
{', '.join(PARAM_WHITELIST)}

## Vincoli rigidi inviolabili
1. n-gpu-layers deve essere sempre "all". Non tentare mai lo scarico parziale.
2. Se la baseline ha gia' la decodifica speculativa abilitata (spec-type=draft-mtp), non disattivarla. La decodifica speculativa e' la leva di velocita' piu' grande.
3. Deve includere --fit off
4. Il valore di flash-attn puo' essere solo "on" o "off"
5. Ordine di declassamento quando la VRAM non basta: f16 → q8_0 → q4_0 (si abbassa la quantizzazione della cache), mai ridurre gli strati su GPU
6. ctx-size e' fissato dall'utente: e' vietato emetterlo o modificarlo in params (anche con VRAM insufficiente non si tocca, se serve si abbassa la quantizzazione della cache)

## Direzioni che puoi esplorare (in ordine di priorita')
1. spec-draft-n-max: prova 2/3/4/5 (influisce sulla lunghezza di accettazione della decodifica speculativa)
2. batch-size / ubatch-size: rifinitura nell'intervallo ±50% attorno alla baseline
3. cache-type-k/v: se la baseline usa f16, prova q8_0 per vedere se c'e' differenza di velocita' (di solito differenza <5%)
4. threads: rifinitura di ±2 attorno al numero di core fisici
5. parallel: se c'e' esigenza di concorrenza si puo' provare 2
6. Se la baseline non ha la decodifica speculativa abilitata (VRAM insufficiente), non forzarla

## Formato di output (JSON rigoroso)
A ogni round devi emettere un oggetto JSON:

Se vuoi testare un gruppo di parametri:
{{"action": "test", "params": {{"nome_parametro": "valore", ...}}, "reasoning": "breve analisi del perche' hai scelto questo gruppo di parametri"}}

Se ritieni di aver trovato l'ottimo o di non poter ottimizzare oltre:
{{"action": "done", "params": {{"nome_parametro": "valore", ...}}, "reasoning": "spiegazione dell'analisi della raccomandazione finale", "confidence": "high/medium/low"}}

Nota:
- Tutti i valori in params sono stringhe
- Non emettere nulla al di fuori del JSON
- A ogni round emetti un solo gruppo di parametri
- Cambia solo 1-2 parametri alla volta, non troppi insieme (altrimenti non si capisce quale cambiamento sia efficace)
"""


def _build_test_result_message(round_num: int, params: dict, metrics: dict) -> str:
    """Costruisce il messaggio di feedback con i risultati della misura (include metriche CPU/memoria, per un'analisi completa dell'AI)"""
    mem_info = ""
    if metrics.get("mem_total_gb"):
        mem_info = f"- Memoria di sistema: {metrics.get('mem_used_gb', 0)}GB / {metrics.get('mem_total_gb', 0)}GB ({metrics.get('mem_pct', 0)}%)\n"
    return f"""Risultati del test del round {round_num}:

Parametri testati: {json.dumps(params, ensure_ascii=False)}

[Prestazioni di inferenza]
- Velocita' di decodifica: {metrics.get('decode', 0)} t/s
- Velocita' di prefill: {metrics.get('prefill', 0)} t/s
- Latenza al primo token (TTFT): {metrics.get('ttft_ms', 0)} ms

[Stato GPU]
- Utilizzo GPU: {metrics.get('gpu_util', 0)}%
- Occupazione VRAM GPU: {metrics.get('gpu_mem_pct', 0)}%

[CPU e memoria]
- Utilizzo CPU: {metrics.get('cpu_pct', 0)}%
{mem_info}
Analizza tutte le metriche precedenti nel loro insieme:
- Utilizzo GPU basso ma VRAM piena -> probabilmente memory-bound (normale), non ridurre gli strati per questo
- Utilizzo CPU troppo alto -> forse threads impostato male o qualche strato e' finito su CPU
- Occupazione di memoria vicina al totale -> rischio di swap, serve abbassare ctx o cache
- Se c'e' ancora margine di ottimizzazione, emetti action=test e una nuova combinazione di parametri
- Se e' gia' ottimale o non si puo' migliorare oltre, emetti action=done e la raccomandazione finale
"""


# ==================== Chiamata all'LLM ====================

def _call_llm(cfg: dict, messages: List[dict], job_id: str = None) -> Optional[str]:
    """Chiama l'API compatibile OpenAI, restituisce il contenuto del messaggio assistant.
    In caso di errore scrive la causa reale nel log del job (prima un except Exception la inghiottiva e non si riusciva a individuarla).
    """
    def _log(msg: str):
        if job_id:
            _append_log(job_id, msg)

    url = cfg.get("api_url", "").rstrip("/")
    if not url:
        _log("  LLM fallito: indirizzo API vuoto, compilare prima api_url nelle impostazioni di tuning AI")
        return None
    if not url.endswith("/chat/completions"):
        if "/v1" in url:
            url = f"{url}/chat/completions"
        else:
            url = f"{url}/v1/chat/completions"
    _log(f"  → Richiesta all'LLM: {url} | model={cfg.get('model_name', '')}")

    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    else:
        _log("  ⚠ API Key non configurata (se il servizio richiede autenticazione restituira' 401)")

    payload = json.dumps({
        "model": cfg.get("model_name", ""),
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2048,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            # Richiesta riuscita ma senza choices: di solito il nome del modello e' sbagliato o la struttura della risposta e' anomala
            _log(f"  L'LLM ha restituito risposta senza choices, risposta grezza: {json.dumps(data, ensure_ascii=False)[:400]}")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="replace")[:400]
        except Exception:
            pass
        _log(f"  Errore HTTP {e.code} dell'LLM: {body}")
    except urllib.error.URLError as e:
        _log(f"  Errore di rete dell'LLM (indirizzo irraggiungibile/timeout/DNS): {e.reason}")
    except Exception as e:
        _log(f"  Eccezione nella chiamata all'LLM: {type(e).__name__}: {e}")
    return None


def _parse_llm_response(text: str) -> Optional[dict]:
    """Interpreta il JSON restituito dall'LLM, con tolleranza agli errori"""
    if not text:
        return None
    # Prova l'interpretazione diretta
    text = text.strip()
    # Toglie un eventuale blocco di codice markdown
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Prova a cercare una sottostringa JSON
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return None


def _validate_params(params: dict) -> dict:
    """Filtra i parametri fuori whitelist, restituisce il sottoinsieme valido; normalizza i booleani"""
    valid = {}
    for k, v in params.items():
        if k in PARAM_WHITELIST:
            valid[k] = str(v)
    # Normalizzazione: flash-attn riconosce solo on/off, non true/false
    if "flash-attn" in valid:
        fa = valid["flash-attn"].lower()
        if fa in ("true", "1", "yes", "enabled"):
            valid["flash-attn"] = "on"
        elif fa in ("false", "0", "no", "disabled"):
            valid["flash-attn"] = "off"
    # ctx-size e' un vincolo fissato dall'utente, all'AI e' assolutamente vietato modificarlo (difesa nel caso l'AI lo restituisca comunque)
    valid.pop("ctx-size", None)
    # Aggiunge forzatamente i parametri necessari
    valid["fit"] = "off"
    valid["metrics"] = ""
    valid["host"] = "0.0.0.0"
    return valid


# ==================== Task dell'Agent ====================

_JOBS: dict = {}
_LOCK = threading.Lock()


def _append_log(job_id: str, msg: str):
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job["logs"].append({"t": time.strftime("%H:%M:%S"), "msg": msg})


def get_job(job_id: str) -> Optional[dict]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_active_jobs(target_id: str) -> list:
    """Restituisce il riepilogo del task di tuning AI in corso sulla macchina target, per riprendere il polling dopo un refresh del frontend."""
    with _LOCK:
        out = []
        for job in _JOBS.values():
            if job.get("target_id") != target_id:
                continue
            if job.get("status") != "running":
                continue
            logs = job.get("logs", [])
            out.append({
                "job_id": job["job_id"],
                "model": job.get("model", ""),
                "ctx_size": job.get("ctx_size", 0),
                "goal": job.get("goal", ""),
                "status": "running",
                "log_count": len(logs),
                "last_logs": logs[-8:],
                "round_count": len(job.get("rounds", [])),
            })
        return out


def start_ai_tune(target_id: str, model: str, ctx_size: int,
                  goal: str, user_desc: str) -> dict:
    """Avvia il task di tuning con AI Agent"""
    target = get_target(target_id)
    if not target:
        return {"ok": False, "message": "Macchina target inesistente"}
    if not target.engine_path:
        return {"ok": False, "message": "Motore di inferenza non configurato"}
    if getattr(target, "engine_type", "llama_cpp") != "llama_cpp":
        return {"ok": False, "message": "Il tuning AI per ora supporta solo il motore llama.cpp (il sistema di parametri di vLLM e' diverso, non ancora supportato)"}

    cfg = get_config()
    if not cfg.get("api_url"):
        return {"ok": False, "message": "API AI non configurata, configurarla prima nelle Impostazioni"}

    job_id = uuid.uuid4().hex[:8]
    with _LOCK:
        _JOBS[job_id] = {
            "job_id": job_id, "target_id": target_id, "model": model,
            "ctx_size": ctx_size, "goal": goal,
            "status": "running", "logs": [], "rounds": [],
            "best": None, "error": "",
        }

    def _worker():
        executor = None
        try:
            from .executor import make_executor
            from .collectors import path_join, detect_hardware
            executor = make_executor(target)
            engine = LlamaCppAdapter(executor, target)

            if not engine.check_installed():
                _fail(job_id, "Motore di inferenza non rilevato sulla macchina target")
                return

            # Raccolta delle informazioni hardware
            _append_log(job_id, "Raccolta delle informazioni hardware...")
            hardware = detect_hardware(executor, target)
            hardware["os"] = target.os

            # Informazioni sul modello
            model_path = path_join(target, target.models_dir, model)
            model_size_gb = _get_model_size(executor, target, model_path)
            model_info = {"filename": model, "size_gb": model_size_gb}

            _append_log(job_id, f"Hardware: {hardware.get('gpu', {}).get('name', '?')} | "
                                f"modello: {model} ({model_size_gb}GB) | ctx: {ctx_size}")

            # ===== Nuova architettura: il generatore deterministico produce la baseline -> misura reale -> alimenta l'LLM =====
            gpu_info = hardware.get("gpu", {})
            cpu_info = hardware.get("cpu", {})
            gpu_vram = gpu_info.get("total_memory_gb", 8)
            cpu_cores = cpu_info.get("cores", 8)
            cpu_threads = cpu_info.get("threads", 16)

            # Priorita' della sorgente di baseline: ultimo risultato di tuning > generatore deterministico
            from .tune_history import get_latest as _hist_get
            _hist = _hist_get(target_id, model)
            if _hist and _hist.get("params"):
                baseline_params = dict(_hist["params"])
                _src = "tuning automatico" if _hist.get("source") == "tuner" else "tuning AI"
                _append_log(job_id, f"Si usa come baseline l'ultimo risultato di tuning ({_src}, "
                                    f"misurato {_hist.get('score', 0)} t/s, {_hist.get('ts', '')})")
                _append_log(job_id, f"  Parametri: {json.dumps(baseline_params, ensure_ascii=False)}")
            else:
                _append_log(job_id, "Generazione della configurazione di base deterministica...")
                gen_result = generate_config(
                    gpu_vram_gb=gpu_vram,
                    model_size_gb=model_size_gb,
                    model_filename=model,
                    ctx_size=ctx_size,
                    cpu_cores=cpu_cores,
                    cpu_threads=cpu_threads,
                )
                baseline_params = gen_result["params"]
                for r in gen_result["reasoning"]:
                    _append_log(job_id, f"  · {r}")
                for w in gen_result.get("warnings", []):
                    _append_log(job_id, f"  ⚠ {w}")

            # Misura reale della configurazione di baseline
            _append_log(job_id, "Misura reale della configurazione di baseline...")
            valid_baseline = _validate_params(baseline_params)
            _append_log(job_id, f"  Parametri: {json.dumps(valid_baseline, ensure_ascii=False)}")
            baseline_metrics = _run_test(executor, target, engine, model_path,
                                         valid_baseline, ctx_size, job_id)

            if baseline_metrics:
                _append_log(job_id, f"  ✓ Baseline misurata: decodifica {baseline_metrics.get('decode', 0)} t/s | "
                                    f"prefill {baseline_metrics.get('prefill', 0)} t/s | "
                                    f"GPU {baseline_metrics.get('gpu_util', 0)}%")
            else:
                _append_log(job_id, "  ⚠ Misura della baseline non riuscita, l'AI partira' da zero")

            # Registra la baseline come round 0
            with _LOCK:
                _JOBS[job_id]["rounds"].append({
                    "round": 0,
                    "params": valid_baseline,
                    "metrics": baseline_metrics,
                    "reasoning": "Output del generatore deterministico (non AI)",
                })

            # Costruisce la conversazione con l'LLM (incluse le informazioni di baseline)
            system_prompt = _build_system_prompt(
                hardware, model_info, ctx_size, goal, user_desc,
                baseline_params=valid_baseline,
                baseline_metrics=baseline_metrics,
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "La baseline e' stata misurata, esplora a partire da essa possibili miglioramenti di prestazioni. Cambia solo 1-2 parametri alla volta."},
            ]

            best_result = None
            best_score = baseline_metrics.get("decode", 0) if baseline_metrics else -1
            if baseline_metrics:
                best_result = {"params": valid_baseline, "metrics": baseline_metrics, "round": 0}

            for round_num in range(1, MAX_ROUNDS + 1):
                _append_log(job_id, f"[Round {round_num}/{MAX_ROUNDS}] chiamata all'AI per l'analisi...")

                # Chiama l'LLM
                response = _call_llm(cfg, messages, job_id)
                if response is None:
                    _fail(job_id, f"Chiamata all'LLM non riuscita al round {round_num} (causa nei log sopra)")
                    return

                parsed = _parse_llm_response(response)
                if parsed is None:
                    _append_log(job_id, f"  ⚠ Risposta dell'AI non interpretabile, contenuto grezzo: {response[:200]}")
                    # Rimanda l'errore all'LLM perche' riprovi
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": "Il tuo output non e' JSON valido, riemettilo rispettando rigorosamente il formato."})
                    continue

                action = parsed.get("action", "")
                reasoning = parsed.get("reasoning", "")
                params = parsed.get("params", {})

                _append_log(job_id, f"  Analisi dell'AI: {reasoning[:150]}")

                if action == "done":
                    _append_log(job_id, f"  ✓ L'AI ritiene di aver trovato l'ottimo (confidenza: {parsed.get('confidence', '?')})")
                    final_params = _validate_params(params)
                    with _LOCK:
                        job = _JOBS[job_id]
                        job["best"] = {
                            "params": final_params,
                            "reasoning": reasoning,
                            "confidence": parsed.get("confidence", "medium"),
                            "round": round_num,
                        }
                        job["status"] = "success"
                        _tid, _model, _ctx = job["target_id"], job["model"], job["ctx_size"]
                    _append_log(job_id, f"✓ Tuning AI completato, parametri consigliati: {json.dumps(final_params, ensure_ascii=False)}")
                    # Salva su disco gli ultimi parametri di tuning, per riempire i default della pagina Deploy
                    try:
                        from .tune_history import save_latest
                        save_latest(_tid, _model, _ctx, final_params,
                                    source="ai_tuner", score=best_score)
                    except Exception as e:
                        _append_log(job_id, f"  ⚠ Salvataggio su disco del risultato di tuning non riuscito: {e}")
                    return

                if action != "test":
                    _append_log(job_id, f"  ⚠ action sconosciuta: {action}, si chiede all'AI di riprovare")
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": "action deve essere test o done, riemetti l'output."})
                    continue

                # Esegue il test
                valid_params = _validate_params(params)
                _append_log(job_id, f"  Parametri di test: {json.dumps(valid_params, ensure_ascii=False)}")

                metrics = _run_test(executor, target, engine, model_path,
                                    valid_params, ctx_size, job_id)

                if metrics is None:
                    _append_log(job_id, "  ✗ Test non riuscito (timeout di avvio o eccezione)")
                    test_msg = f"Test del round {round_num} non riuscito: timeout di avvio del modello o parametri non validi. Riprova con un altro gruppo di parametri."
                else:
                    score = metrics.get("decode", 0)
                    _append_log(job_id, f"  Risultato: decodifica {metrics['decode']} t/s | "
                                        f"prefill {metrics['prefill']} t/s | "
                                        f"GPU {metrics['gpu_util']}% | VRAM {metrics['gpu_mem_pct']}% | "
                                        f"CPU {metrics.get('cpu_pct', 0)}% | "
                                        f"memoria {metrics.get('mem_used_gb', 0)}/{metrics.get('mem_total_gb', 0)}GB")
                    test_msg = _build_test_result_message(round_num, valid_params, metrics)

                    # Registra il migliore
                    if score > best_score:
                        best_score = score
                        best_result = {"params": valid_params, "metrics": metrics, "round": round_num}

                # Registra questo round
                with _LOCK:
                    _JOBS[job_id]["rounds"].append({
                        "round": round_num,
                        "params": valid_params,
                        "metrics": metrics,
                        "reasoning": reasoning,
                    })

                # Rimanda i risultati
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": test_msg})

            # Raggiunto il numero massimo di round
            _append_log(job_id, f"Raggiunto il numero massimo di round {MAX_ROUNDS}, si usa il miglior risultato storico")
            if best_result:
                with _LOCK:
                    job = _JOBS[job_id]
                    job["best"] = {
                        "params": best_result["params"],
                        "reasoning": f"Raggiunto il numero massimo di round, si prende il migliore storico (round {best_result['round']})",
                        "confidence": "medium",
                        "round": best_result["round"],
                    }
                    job["status"] = "success"
            else:
                _fail(job_id, "Tutti i round sono falliti")

        except Exception as e:
            _fail(job_id, str(e))
            _append_log(job_id, f"✗ Eccezione: {e}")
        finally:
            try:
                if executor:
                    LlamaCppAdapter(executor, target).stop()
            except Exception:
                pass
            if executor:
                executor.close()

    threading.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "job_id": job_id}


def _run_test(executor: Executor, target: Target, engine: LlamaCppAdapter,
              model_path: str, params: dict, ctx_size: int, job_id: str) -> Optional[dict]:
    """Avvia il modello -> misura -> arresto, restituisce metrics o None"""
    from .tuner import _wait_ready, _bench_median

    engine.stop()
    time.sleep(2)

    # Costruisce l'elenco dei parametri (ctx-size/port/metrics/host vengono aggiunti in modo uniforme sotto, per evitare duplicati)
    args = []
    for k, v in params.items():
        if k in ("metrics", "host", "ctx-size", "port"):
            continue
        if v == "":
            args.append(f"--{k}")
        else:
            args += [f"--{k}", str(v)]
    args += [
        "--ctx-size", str(ctx_size),
        "--metrics",
        "--host", "0.0.0.0",
        "--port", str(target.service_port),
    ]

    ok, msg = engine.start(StartParams(model_path=model_path, extra_args=args))
    if not ok:
        _append_log(job_id, f"  Avvio non riuscito: {msg}")
        return None

    if not _wait_ready(executor, target):
        _append_log(job_id, "  Timeout di avvio")
        engine.stop()
        return None

    metrics = _bench_median(executor, target, ctx_size)
    engine.stop()
    time.sleep(2)
    return metrics


def _get_model_size(executor: Executor, target: Target, model_path: str) -> float:
    """Rileva la dimensione del file del modello in GB"""
    if target.os == "windows":
        cmd = (f'powershell -Command "if(Test-Path \'{model_path}\')'
               f'{{(Get-Item \'{model_path}\').Length}}else{{0}}"')
    else:
        cmd = f'stat -c %s "{model_path}" 2>/dev/null || echo 0'
    r = executor.run(cmd, timeout=10)
    digits = "".join(c for c in r.stdout if c.isdigit())
    if digits:
        return round(int(digits) / (1024 ** 3), 1)
    return 0.0


def _fail(job_id: str, err: str):
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job["status"] = "failed"
            job["error"] = err
    _append_log(job_id, f"✗ {err}")
