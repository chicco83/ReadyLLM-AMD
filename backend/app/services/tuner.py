"""Servizio di tuning con test di carico (versione di ricerca in due fasi)

Nella configurazione hardware attuale dell'utente, cerca i parametri di inferenza ottimali per un modello sulla macchina target.

Punti di progetto:
  1. I parametri sono di tre tipi:
     - Vincoli (fissati da utente/hardware): modello, porta, minimo di ctx -> non si cercano, sono vincoli rigidi
     - Discreti ad alto impatto: spec-type / cache-type-k,v / n-gpu-layers -> cercati nella fase coarse
     - Continui di rifinitura: batch-size / ubatch-size / spec-draft-n-max -> discesa per coordinate nella fase fine
  2. Pre-verifica di fattibilita' della VRAM: stima pesi+KV+cache, le combinazioni che non ci stanno vengono saltate subito, senza sprecare tempo di avvio
  3. Ricerca in due fasi: coarse individua il fattore dominante, fine converge attorno alla combinazione migliore
  4. Misure affidabili: warmup + 3 prove ufficiali con mediana, registrando decodifica/prefill/TTFT/utilizzo GPU
  5. Confronto con la baseline: si misura per prima la configurazione originale dell'utente come baseline, e si produce "consigliata vs attuale"

Obiettivo di ottimizzazione a scelta: latency (percezione end-to-end, default) / throughput (pura velocita' di decodifica) / prefill (prefill di testi lunghi).
Senza motore/senza modello restituisce un errore esplicito, mai dati simulati.
"""

import threading
import re
import time
import json
import uuid
from statistics import median
from typing import Optional, List, Dict

from .executor import Executor
from .engine_adapter import StartParams
from .llama_cpp import LlamaCppAdapter
from ..models.target import Target, get_target

_JOBS: dict = {}
_LOCK = threading.Lock()

# Prompt del benchmark (fisso, garantisce la confrontabilita' tra i gruppi)
_BENCH_PROMPT = "Spiega in una frase che cos'e' un modello linguistico di grandi dimensioni."
# Prompt lungo: circa 2000+ token, sufficiente a innescare piu' suddivisioni di batch e a misurare il vero collo di bottiglia del prefill
_BENCH_LONG_PROMPT = (
    "L'architettura Transformer e' alla base dei moderni modelli linguistici di grandi dimensioni. I suoi componenti principali comprendono il meccanismo di auto-attenzione multi-testa, "
    "la codifica posizionale, la rete neurale feed-forward e la normalizzazione degli strati. Il meccanismo di auto-attenzione permette al modello, nell'elaborare ogni parola, di prestare attenzione a "
    "tutte le altre posizioni della sequenza di input, catturando cosi' le dipendenze a lungo raggio. L'attenzione multi-testa proietta lo spazio di rappresentazione "
    "in piu' sottospazi calcolando l'attenzione in parallelo, aumentando la capacita' espressiva del modello. La codifica posizionale usa funzioni seno e coseno "
    "per generare una rappresentazione vettoriale unica per ogni posizione della sequenza, permettendo al modello di percepire l'ordine delle parole. "
    "La rete feed-forward applica a ogni posizione, in modo indipendente, due trasformazioni completamente connesse, introducendo la capacita' di estrarre caratteristiche non lineari. "
    "La normalizzazione degli strati e le connessioni residue garantiscono la stabilita' dell'addestramento delle reti profonde. In fase di inferenza, il meccanismo della cache KV "
    "evita di ricalcolare le coppie chiave-valore delle posizioni gia' elaborate, migliorando in modo significativo l'efficienza della generazione autoregressiva. "
    "La tecnica della decodifica speculativa fa predire a un piccolo modello bozza piu' token candidati, che il modello grande verifica poi in parallelo, "
    "accelerando la generazione senza perdere qualita'. Le tecniche di quantizzazione riducono l'occupazione di VRAM e il carico di calcolo abbassando la "
    "precisione numerica di pesi e attivazioni, permettendo a modelli piu' grandi di girare su hardware consumer. "
    "Gli schemi di quantizzazione piu' comuni comprendono GPTQ, AWQ e i vari livelli di quantizzazione del formato GGML come Q4_0, Q4_K_M, "
    "Q5_K_M, Q8_0 ecc., che offrono compromessi diversi tra perdita di precisione e tasso di compressione. "
    "L'algoritmo Flash Attention, con il calcolo a blocchi e il trucco del softmax online, riduce la complessita' di VRAM dell'attenzione "
    "da quadratica a lineare, rendendo possibile elaborare contesti molto lunghi. PagedAttention organizza invece la cache KV "
    "in una struttura a tabelle di pagine simile alla memoria virtuale dei sistemi operativi, supportando una gestione efficiente della VRAM e la concorrenza di piu' richieste. "
    "A livello di deploy, le strategie di parallelismo del modello comprendono il parallelismo tensoriale, a pipeline e di sequenza, adatte rispettivamente a "
    "topologie hardware e dimensioni di modello diverse. I motori di inferenza come llama.cpp, vLLM, TensorRT-LLM ecc. "
    "sono stati ottimizzati in profondita' ciascuno per piattaforme hardware e obiettivi diversi. Il continuous batching permette di inserire dinamicamente "
    "nuove richieste nel batch in corso di elaborazione, aumentando l'utilizzo della GPU e il throughput del sistema. "
    "Le varianti della decodifica speculativa comprendono Medusa, EAGLE e Lookahead Decoding, che con strategie diverse di "
    "generazione della bozza trovano un equilibrio tra velocita' e qualita'. Le tecniche di distillazione e potatura del modello riducono invece "
    "il fabbisogno di calcolo dal lato della struttura del modello: la distillazione della conoscenza fa imparare a un modello piccolo la distribuzione di output di uno grande, la potatura strutturata rimuove le "
    "teste di attenzione e i neuroni ridondanti. I modelli a miscela di esperti instradano l'input verso pochi sotto-reti esperte tramite una rete di gating, "
    "riducendo molto il calcolo di ogni passaggio in avanti pur mantenendo il numero di parametri. L'uso combinato di queste tecniche rende possibile "
    "distribuire su una singola scheda grafica consumer modelli con miliardi di parametri, ponendo le basi per le applicazioni di IA locali. "
) * 6  # ×6 ≈ 2400+ tokens
_BENCH_MAX_TOKENS = 128
_BENCH_REPEATS = 3  # numero di ripetizioni della misura ufficiale, si prende la mediana

# ==================== Stratificazione dei parametri ====================

# Parametri discreti ad alto impatto: cercati nella fase coarse
SPEC_OPTIONS = ["off", "draft-mtp"]           # modalita' di decodifica speculativa
CACHE_OPTIONS = ["f16", "q8_0", "q4_0"]       # quantizzazione della KV cache (piu' risparmia VRAM, piu' ctx grande)
NGL_OPTIONS = ["all", "0"]                    # strati scaricati sulla GPU (all = tutti in GPU; 0 = tutto su CPU come ripiego)

# Parametri continui di rifinitura: discesa per coordinate nella fase fine
CONTINUOUS_GRID = {
    "batch-size": [1024, 2048, 4096, 8192],
    "ubatch-size": [128, 256, 512, 1024],
    "threads": [16, 24, 32],            # thread CPU, influiscono su prefill e cooperazione lato CPU
    "spec-draft-n-max": [2, 3, 4, 5],   # quanti token predice la speculazione in una volta
    "spec-draft-n-min": [1, 2, 3],      # soglia minima di accettazione della speculazione, influisce sull'efficienza speculativa
}

# Pesi di punteggio per obiettivo: (velocita' di decodifica, velocita' di prefill, TTFT)
GOAL_WEIGHTS = {
    "latency":    {"decode": 0.5, "prefill": 0.3, "ttft": 0.2},
    "throughput": {"decode": 1.0, "prefill": 0.0, "ttft": 0.0},
    "prefill":    {"decode": 0.2, "prefill": 0.8, "ttft": 0.0},
    # [2026-10-02 v1.1.27] Coding / agenti (Claude Code, Cline, Continue...): a ogni richiesta si rileggono file e cronologia
    # (prompt lunghi -> il prefill pesa molto) e poi si genera codice (la decodifica conta ancora); il TTFT conta poco.
    "coding":     {"decode": 0.4, "prefill": 0.5, "ttft": 0.1},
}
GOAL_LABELS = {
    "latency": "Percezione end-to-end",
    "throughput": "Throughput di decodifica",
    "prefill": "Prefill di testi lunghi",
    "coding": "Coding e agenti",   # [2026-10-02 v1.1.27]
}


def _normalize_cfg(spec_type, cache_type, ngl, batch, ubatch, draft_n_max) -> dict:
    """Una configurazione completa = fattori dominanti discreti + parametri continui di rifinitura"""
    cfg = {
        "spec-type": spec_type,
        "cache-type-k": cache_type,
        "cache-type-v": cache_type,
        "n-gpu-layers": ngl,
        "batch-size": str(batch),
        "ubatch-size": str(ubatch),
        "threads": "24",
    }
    if spec_type != "off":
        cfg["spec-draft-n-max"] = str(draft_n_max)
        cfg["spec-draft-n-min"] = "2"
    return cfg


def _cfg_label(cfg: dict) -> str:
    return " / ".join(f"{k}={v}" for k, v in cfg.items())


def _args_list(cfg: dict, target: Target, ctx_size: int) -> List[str]:
    """Converte il dict di configurazione nell'elenco di parametri da riga di comando di llama-server (con voci fisse e ctx)"""
    args = []
    for k, v in cfg.items():
        # [2026-10-01 v1.1.19] «off» e' un valore interno del tuner, NON valido per llama-server (--spec-type accetta none,
        # draft-mtp, ...): con "--spec-type off" il server usciva subito (dopo la baseline ogni prova falliva).
        # Con spec-type=off il parametro viene semplicemente omesso (= none, il predefinito).
        # Versione precedente: nessun filtro, si passava sempre --spec-type <valore>
        if k == "spec-type" and str(v) in ("off", "none", ""):
            continue
        if k == "n-gpu-layers":
            # llama-server riconosce sia --n-gpu-layers sia --gpu-layers, si usa il nome standard
            args += ["--n-gpu-layers", str(v)]
        else:
            args += [f"--{k}", str(v)]
    args += [
        "--ctx-size", str(ctx_size),
        "--flash-attn", "on",
        # Punto chiave: fit va disattivato esplicitamente. fit e' on di default e «di testa sua» abbassa batch/ubatch impostati da noi
        # per far stare tutto nel margine di VRAM che ritiene sicuro, per cui i parametri realmente attivi durante la ricerca != quelli che misuriamo e il risultato e' falsato.
        "--fit", "off",
        "--metrics",
        "--host", "0.0.0.0",
        "--port", str(target.service_port),
    ]
    return args


# ==================== Pre-verifica di fattibilita' della VRAM ====================

# Byte per elemento della quantizzazione della KV cache
_CACHE_BYTES = {"f16": 2.0, "q8_0": 1.0, "q4_0": 0.5}


def _estimate_vram_gb(model_size_gb: float, ctx_size: int,
                      cache_type: str, kv_heads_dim: int = 1024, calib: Optional[dict] = None) -> float:
    """Stima approssimativa dell'occupazione di VRAM in GB: pesi + KV cache + buffer di calcolo.

    [2026-10-01 v1.1.18] Due correzioni, dal tuning dell'utente (9B Q8_0, ctx 262144, 15.8 GB di VRAM): la vecchia stima
    (kv_dim 8192 = attenzione senza GQA, 64 strati, tarata su un 27B) scartava TUTTE le combinazioni GPU anche se la baseline
    con cache f16 girava benissimo in GPU, e il tuning ripiegava su una CPU lentissima.
      1) calibrazione REALE: se una prova precedente ha loggato "llama_kv_cache: size = X MiB", si usa quel valore (scalato per
         il tipo di cache) invece della stima;
      2) senza calibrazione: kv_dim 1024 (GQA, 8 KV head x 128) e numero di strati dedotto dalla dimensione del modello.
    Versione precedente: kv_bytes = 2 * ctx_size * 8192 * bytes; kv_gb = kv_bytes * 64 / 1024**3  (sempre 64 strati)
    """
    if calib and calib.get("kv_gb"):
        # la KV misurata era con calib["cache"]: si riporta a f16 e poi al tipo richiesto
        f16 = calib["kv_gb"] / (_CACHE_BYTES.get(calib.get("cache", "f16"), 2.0) / 2.0)
        kv_gb = f16 * (_CACHE_BYTES.get(cache_type, 2.0) / 2.0)
    else:
        layers = max(24, min(80, int(model_size_gb * 3.5)))
        kv_gb = 2 * ctx_size * kv_heads_dim * _CACHE_BYTES.get(cache_type, 2.0) * layers / (1024 ** 3)
    return model_size_gb + kv_gb + 1.0     # +1 GB: buffer di calcolo / contesto GPU


_KV_RE = re.compile(r"llama_kv_cache\w*:\s*size\s*=\s*([\d.]+)\s*MiB", re.I)


def _kv_gb_from_log(executor: Executor, target: Target) -> float:
    """Somma le righe «llama_kv_cache: size = X MiB» dell'ultimo avvio di llama-server (0 se assenti)."""
    righe = _log_server_tail(executor, target, 400)
    tot = sum(float(m.group(1)) for ln in righe for m in [_KV_RE.search(ln)] if m)
    return round(tot / 1024.0, 3)


def _fits_vram(cfg: dict, model_size_gb: float, ctx_size: int, gpu_vram_gb: float,
               calib: Optional[dict] = None) -> bool:
    """Stabilisce se la configurazione ci sta in VRAM; n-gpu-layers=0 e' un ripiego su CPU e «ci sta» sempre (lento)"""
    if cfg.get("n-gpu-layers") == "0":
        return True
    est = _estimate_vram_gb(model_size_gb, ctx_size, cfg.get("cache-type-k", "f16"), calib=calib)
    # Lascia il 5% di margine per frammentazione della VRAM (prima 10%: con la stima corretta basta meno)
    return est <= gpu_vram_gb * 0.95


def _get_calib(job_id: str) -> Optional[dict]:
    with _LOCK:
        return (_JOBS.get(job_id) or {}).get("kv_calib")


# ==================== Baseline = parametri del Deploy; supporto MTP (v1.1.11, 2026-10-01) ====================
# Prima la baseline era FISSA nel frontend (draft-mtp / q4_0 / batch 4096): con un modello o una build senza MTP il
# primo avvio andava in timeout e il tuning ripartiva da una base sbagliata. Ora la baseline e' la riga di parametri
# del Deploy per quel modello (ultimo tuning o generatore deterministico), modificabile; e draft-mtp viene proposto
# solo se supportato sia dal modello (nome file) sia dalla build di llama-server (--help cita "mtp").

# Parametri gestiti da _args_list (aggiunti a parte) o dal Deploy: non vanno nel dict di configurazione
_ARGS_ESCLUSI = {"ctx-size", "c", "port", "host", "metrics", "flash-attn", "fit", "model", "m", "no-webui", "parallel"}
_SPEC_KEYS = ("spec-type", "spec-draft-n-max", "spec-draft-n-min")
_MTP_BUILD_CACHE: dict = {}


def parse_args_to_cfg(args_str: str) -> dict:
    """Converte "--cache-type-k q4_0 --batch-size 4096 ..." nel dict di configurazione del tuner (chiavi senza --)."""
    toks = (args_str or "").split()
    cfg, i = {}, 0
    while i < len(toks):
        t = toks[i]
        if t.startswith("--") and len(t) > 2:
            key = t[2:]
            if i + 1 < len(toks) and not toks[i + 1].startswith("--"):
                val = toks[i + 1]
                i += 1
            else:
                val = ""
            if key == "gpu-layers":
                key = "n-gpu-layers"
            if key not in _ARGS_ESCLUSI and val != "":
                cfg[key] = val
        i += 1
    return cfg


def cfg_to_args(cfg: dict) -> str:
    return " ".join(f"--{k} {v}" for k, v in cfg.items())


def is_spec_key(k: str) -> bool:
    """Parametro di decodifica speculativa/draft (spec-type, spec-draft-*, spec-draft-ngl, gpu-layers-draft, ...)."""
    return k.startswith("spec-") or "draft" in k


def strip_mtp(cfg: dict) -> dict:
    """Toglie dal dict i parametri di decodifica speculativa MTP.
    [2026-10-01 v1.1.11] prima solo _SPEC_KEYS (3 chiavi): restavano --gpu-layers-draft e --spec-draft-ngl, che con
    un llama-server senza MTP fanno fallire l'avvio. Versione precedente: {k: v ... if k not in _SPEC_KEYS}"""
    return {k: v for k, v in cfg.items() if not is_spec_key(k)}


def mtp_state(executor: Executor, target: Target, model: str) -> dict:
    """{"model": bool, "build": bool|None, "allowed": bool}: MTP e' proponibile con questo modello e questa build?
    model: dedotto dal nome del file (config_generator._supports_mtp). build: llama-server --help cita 'mtp'?
    (None se non si riesce a interrogare: in tal caso conta solo il modello)."""
    from .config_generator import _supports_mtp
    model_ok = bool(_supports_mtp(model))
    key = (target.id, target.engine_path)
    if key not in _MTP_BUILD_CACHE:
        try:
            r = executor.run(f'"{target.engine_path}" --help 2>&1', timeout=40)
            txt = (r.stdout or "") + (r.stderr or "")
            _MTP_BUILD_CACHE[key] = ("mtp" in txt.lower()) if len(txt) > 200 else None
        except Exception:
            _MTP_BUILD_CACHE[key] = None
    build = _MTP_BUILD_CACHE[key]
    return {"model": model_ok, "build": build, "allowed": model_ok and build is not False}


# ==================== Misura della velocita' ====================

# [2026-10-01 v1.1.8] timeout 120 -> 300 s: un modello Q8_0 letto da disco lento / Drive puo' superare 2 minuti
# Versione precedente: timeout: int = 120
def _wait_ready(executor: Executor, target: Target, timeout: int = 300) -> bool:
    """Fa polling su /health della macchina target finche' il servizio e' pronto"""
    deadline = time.time() + timeout
    cmd = (f'curl -s -o /dev/null -w "%{{http_code}}" --max-time 3 '
           f'http://127.0.0.1:{target.service_port}/health')
    while time.time() < deadline:
        r = executor.run(cmd, timeout=8)
        if r.stdout.strip() == "200":
            return True
        time.sleep(2)
    return False


def _curl_completion(executor: Executor, target: Target, payload: dict) -> Optional[dict]:
    """Invia un completion al llama-server della macchina target, restituisce il JSON interpretato o None.

    Il corpo della richiesta passa sempre per «write_file scrive JSON UTF-8 grezzo in un file temporaneo + curl @file»:
    il JSON di un prompt lungo puo' arrivare a decine di KB e incorporarlo in base64 nella riga di comando supererebbe il limite di
    8191 caratteri di Windows cmd.exe venendo troncato (leggendo un file vecchio, misure falsate), mentre SFTP/scrittura locale non hanno questo limite.
    """
    body = json.dumps(payload)
    url = f"http://127.0.0.1:{target.service_port}/completion"
    if target.os == "windows":
        json_path = "C:/temp/bench.json"
        # Prima si assicura che la cartella esista (comando breve, senza limite di lunghezza)
        executor.run(
            'powershell -Command "New-Item -ItemType Directory -Force -Path C:\\temp | Out-Null"',
            timeout=15,
        )
        if not executor.write_file(body, json_path):
            return None
        cmd = (f'curl -s --max-time 120 -X POST {url} '
               f'-H "Content-Type: application/json" -d @{json_path}')
    else:
        json_path = "/tmp/bench.json"
        if not executor.write_file(body, json_path):
            return None
        cmd = (f"curl -s --max-time 120 -X POST {url} "
               f"-H 'Content-Type: application/json' -d @{json_path}")
    r = executor.run(cmd, timeout=130)
    if not r.stdout:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def _gpu_snapshot(executor: Executor, target: Target) -> dict:
    """Al momento della misura rileva una volta utilizzo GPU / occupazione VRAM"""
    try:
        from .collectors import _collect_gpu
        return _collect_gpu(executor, target) or {}
    except Exception:
        return {}


def _cpu_mem_snapshot(executor: Executor, target: Target) -> dict:
    """Al momento della misura rileva una volta utilizzo CPU / occupazione memoria"""
    try:
        from .collectors import _collect_cpu_mem
        return _collect_cpu_mem(executor, target) or {}
    except Exception:
        return {}


def _bench_once(executor: Executor, target: Target, ctx_size: int) -> dict:
    """Una misura completa: prompt corto per misurare decodifica+TTFT, prompt lungo per misurare il prefill.
    Restituisce {decode, prefill, ttft_ms, gpu_util, gpu_mem_pct}."""
    # Prompt corto: velocita' di decodifica + TTFT
    short = _curl_completion(executor, target, {
        "prompt": _BENCH_PROMPT,
        "n_predict": _BENCH_MAX_TOKENS,
        "temperature": 0,
        "stream": False,
    })
    decode = 0.0
    ttft_ms = 0.0
    if short:
        tm = short.get("timings", {})
        decode = float(tm.get("predicted_per_second", 0) or 0)
        # TTFT approssimato = tempo di generazione del primo token: rappresentato dal tempo di elaborazione del prompt
        ttft_ms = float(tm.get("prompt_ms", 0) or 0)

    # Prompt lungo: velocita' di prefill
    # Si aggiunge un prefisso casuale univoco: la prefix cache di llama confronta dall'inizio della sequenza, se il prefisso cambia l'intera cache non fa hit,
    # altrimenti con warmup + 3 ripetizioni dello stesso prompt dalla 2a in poi prompt_n crolla e il prefill appare gonfiato a ~40
    import uuid as _uuid
    long = _curl_completion(executor, target, {
        "prompt": f"[{_uuid.uuid4().hex[:16]}] " + _BENCH_LONG_PROMPT,
        "n_predict": 16,
        "temperature": 0,
        "stream": False,
    })
    prefill = 0.0
    if long:
        tm = long.get("timings", {})
        prefill = float(tm.get("prompt_per_second", 0) or 0)

    gpu = _gpu_snapshot(executor, target)
    cpu_mem = _cpu_mem_snapshot(executor, target)
    return {
        "decode": round(decode, 2),
        "prefill": round(prefill, 2),
        "ttft_ms": round(ttft_ms, 1),
        "gpu_util": gpu.get("utilization", 0),
        "gpu_mem_pct": gpu.get("memory_pct", 0),
        "cpu_pct": cpu_mem.get("cpu_pct", 0),
        "mem_used_gb": cpu_mem.get("memory_used_gb", 0),
        "mem_total_gb": cpu_mem.get("memory_total_gb", 0),
        "mem_pct": cpu_mem.get("memory_pct", 0),
    }


def _bench_median(executor: Executor, target: Target, ctx_size: int) -> dict:
    """1 warmup + _BENCH_REPEATS prove ufficiali, per ogni metrica si prende la mediana"""
    _bench_once(executor, target, ctx_size)  # warmup, scartato
    runs = [_bench_once(executor, target, ctx_size) for _ in range(_BENCH_REPEATS)]
    return {
        "decode": round(median(r["decode"] for r in runs), 2),
        "prefill": round(median(r["prefill"] for r in runs), 2),
        "ttft_ms": round(median(r["ttft_ms"] for r in runs), 1),
        "gpu_util": round(median(r["gpu_util"] for r in runs), 1),
        "gpu_mem_pct": round(median(r["gpu_mem_pct"] for r in runs), 1),
        "cpu_pct": round(median(r["cpu_pct"] for r in runs), 1),
        "mem_used_gb": round(median(r["mem_used_gb"] for r in runs), 1),
        "mem_total_gb": round(median(r["mem_total_gb"] for r in runs), 1),
        "mem_pct": round(median(r["mem_pct"] for r in runs), 1),
    }


def _score(metrics: dict, goal: str) -> float:
    """Normalizza e pondera le metriche multiple in un unico punteggio in base all'obiettivo (piu' grande e' meglio).
    Usa scale relative: decodifica/prefill rispetto al proprio massimo, TTFT preso come reciproco."""
    w = GOAL_WEIGHTS.get(goal, GOAL_WEIGHTS["latency"])
    decode = metrics.get("decode", 0)
    prefill = metrics.get("prefill", 0)
    ttft = metrics.get("ttft_ms", 0) or 1.0
    # Riferimento di normalizzazione (limite superiore empirico, serve solo a portare grandezze diverse in un intervallo confrontabile)
    score = (w["decode"] * decode +
             w["prefill"] * (prefill / 100.0) +   # il prefill e' spesso nell'ordine delle migliaia, ridotto di 100 volte
             w["ttft"] * (1000.0 / ttft))         # piu' piccolo e' il TTFT meglio e', si prende il reciproco
    return round(score, 3)


# ==================== Log / Task ====================

def _append_log(job_id: str, msg: str):
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job["logs"].append({"t": time.strftime("%H:%M:%S"), "msg": msg})


def _set_progress(job_id: str, phase: str = None, total: int = None, step: bool = False):
    """[2026-10-01 v1.1.19] Avanzamento per la barra di progresso: fase corrente, prove completate e totale stimato.
    Il totale e' una stima (la fase fine converge in modo dinamico): se le prove superano la stima, il totale si allarga."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        pr = job.setdefault("progress", {"done": 0, "total": 1, "phase": ""})
        if phase is not None:
            pr["phase"] = phase
        if total is not None:
            pr["total"] = max(total, pr["done"])
        if step:
            pr["done"] += 1
            if pr["done"] > pr["total"]:
                pr["total"] = pr["done"]


def get_job(job_id: str) -> Optional[dict]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def get_last_job(target_id: str) -> Optional[dict]:
    """[2026-10-02 v1.1.23] Ultimo tuning (in corso, riuscito o fallito) della macchina: serve a non perdere esito e risultati
    quando la pagina si ricarica o il pannello viene rimontato a fine tuning."""
    with _LOCK:
        jobs = [j for j in _JOBS.values() if j.get("target_id") == target_id]
        return dict(jobs[-1]) if jobs else None


def list_active_jobs(target_id: str) -> list:
    """Restituisce il riepilogo del task di tuning in corso sulla macchina target, per riprendere il polling dopo un refresh del frontend."""
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
                "result_count": len(job.get("results", [])),
                "progress": job.get("progress", {"done": 0, "total": 1, "phase": ""}),
            })
        return out


# ==================== Ricerca in due fasi ====================

def _run_one(executor: Executor, target: Target, engine: LlamaCppAdapter,
             model_path: str, cfg: dict, ctx_size: int, job_id: str,
             tag: str) -> Optional[dict]:
    """Avvia un gruppo di configurazione -> misura -> arresto, restituisce il risultato con le metriche; se l'avvio fallisce restituisce None"""
    label = _cfg_label(cfg)
    _set_progress(job_id, phase=f"{tag}")
    try:
        return _run_one_inner(executor, target, engine, model_path, cfg, ctx_size, job_id, tag, label)
    finally:
        _set_progress(job_id, step=True)


def _run_one_inner(executor, target, engine, model_path, cfg, ctx_size, job_id, tag, label):
    engine.stop()
    time.sleep(2)
    params = StartParams(model_path=model_path, extra_args=_args_list(cfg, target, ctx_size))
    ok, msg = engine.start(params)
    if not ok:
        _append_log(job_id, f"  [{tag}] {label} avvio non riuscito: {msg}")
        for ln in _log_server_tail(executor, target):
            _append_log(job_id, f"    [llama-server] {ln}")
        return None
    if not _wait_ready(executor, target):
        _append_log(job_id, f"  [{tag}] {label} timeout di avvio (forse VRAM insufficiente)")
        # [2026-10-01 v1.1.8] mostra il log di llama-server: indica la causa reale dell'errore
        for ln in _log_server_tail(executor, target):
            _append_log(job_id, f"    [llama-server] {ln}")
        engine.stop()
        return None
    metrics = _bench_median(executor, target, ctx_size)
    # [2026-10-01 v1.1.18] Calibra la stima della KV cache sul valore reale loggato da llama-server
    try:
        kv = _kv_gb_from_log(executor, target)
        if kv > 0:
            with _LOCK:
                _JOBS[job_id]["kv_calib"] = {"kv_gb": kv, "cache": cfg.get("cache-type-k", "f16")}
            _append_log(job_id, f"  KV cache reale: {kv:.2f} GB (cache {cfg.get('cache-type-k', 'f16')}): stime di VRAM calibrate su questo valore")
    except Exception:
        pass
    engine.stop()
    time.sleep(2)
    _append_log(job_id, f"  [{tag}] {label} → decodifica {metrics['decode']} t/s, "
                        f"prefill {metrics['prefill']} t/s, GPU {metrics['gpu_util']}%")
    return {"config": cfg, "label": label, "metrics": metrics}


def _coarse_search(executor, target, engine, model_path, ctx_size,
                   model_size_gb, gpu_vram_gb, goal, job_id, allow_mtp: bool = True,
                   baseline_result: Optional[dict] = None) -> Optional[dict]:
    """Fase uno: cerca i fattori dominanti discreti spec-type x cache-type.
    n-gpu-layers e' all di default (tutto in GPU); si ripiega su 0 solo quando tutte le combinazioni all superano la VRAM,
    per non sprecare tempo di misura trattando come candidate normali combinazioni inevitabilmente lente come far girare un modello grande solo su CPU."""
    def _build(ngl):
        out = []
        # [2026-10-01 v1.1.11] senza supporto MTP si prova solo spec-type=off (prima si provava sempre anche draft-mtp)
        for spec in (SPEC_OPTIONS if allow_mtp else [o for o in SPEC_OPTIONS if o == "off"]):
            for cache in CACHE_OPTIONS:
                cfg = _normalize_cfg(spec, cache, ngl,
                                     CONTINUOUS_GRID["batch-size"][1],
                                     CONTINUOUS_GRID["ubatch-size"][1], 3)
                if _fits_vram(cfg, model_size_gb, ctx_size, gpu_vram_gb, _get_calib(job_id)):
                    out.append(cfg)
                else:
                    _append_log(job_id, f"  Saltata (VRAM insufficiente): {_cfg_label(cfg)}")
        return out

    candidates = _build("all")
    if not candidates:
        # [2026-10-01 v1.1.18] Prima: ripiego su CPU (n-gpu-layers=0), lentissimo e quasi sempre inutile. Ora si provano comunque
        # le combinazioni con cache q4_0 in GPU (la stima e' solo un'approssimazione: vale la prova reale).
        # Versione precedente: _append_log(... "ripiego su CPU (n-gpu-layers=0)"); candidates = _build("0")
        _append_log(job_id, "  La stima della VRAM scarta tutte le combinazioni GPU: provo comunque quelle con cache q4_0 (la prova reale decide)")
        for spec in (SPEC_OPTIONS if allow_mtp else [o for o in SPEC_OPTIONS if o == "off"]):
            candidates.append(_normalize_cfg(spec, "q4_0", "all", CONTINUOUS_GRID["batch-size"][1],
                                             CONTINUOUS_GRID["ubatch-size"][1], 3))

    _append_log(job_id, f"[Fase 1 coarse] {len(candidates)} combinazioni di fattori dominanti")
    # [2026-10-01 v1.1.19] totale stimato = prove gia' fatte (baseline) + coarse + ~8 prove della fase fine
    with _LOCK:
        _done = _JOBS.get(job_id, {}).get("progress", {}).get("done", 0)
    _set_progress(job_id, total=_done + len(candidates) + 8)
    scored = []
    for i, cfg in enumerate(candidates):
        # [2026-10-02 v1.1.25] Se la combinazione e' IDENTICA alla baseline si riusa la misura gia' fatta invece di rimisurarla:
        # la stessa configurazione misurata due volte differisce di qualche punto per rumore (nel test dell'utente 68.2 / 66.0 / 65.5 t/s
        # per la stessa riga) e il tuner la scambiava per «una variante peggiore».
        if baseline_result and {k: str(v) for k, v in cfg.items()} == {k: str(v) for k, v in baseline_result["config"].items()}:
            _append_log(job_id, f"  [coarse {i+1}/{len(candidates)}] identica alla baseline: riuso la misura gia' fatta")
            _set_progress(job_id, step=True)
            r = dict(baseline_result)
        else:
            r = _run_one(executor, target, engine, model_path, cfg, ctx_size,
                         job_id, f"coarse {i+1}/{len(candidates)}")
        if r:
            r["score"] = _score(r["metrics"], goal)
            scored.append(r)

    if not scored:
        return None
    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0]
    _append_log(job_id, f"  Migliore coarse: {best['label']} (punteggio {best['score']})")
    return best


def _fine_search(executor, target, engine, model_path, ctx_size,
                 model_size_gb, gpu_vram_gb, goal, job_id,
                 base_cfg: dict) -> dict:
    """Fase due: attorno al migliore coarse, converge con discesa per coordinate sui parametri continui"""
    current = dict(base_cfg)
    cur = _run_one(executor, target, engine, model_path, current, ctx_size,
                   job_id, "fine base")
    if cur is None:
        return {"config": current, "label": _cfg_label(current),
                "metrics": {"decode": 0, "prefill": 0, "ttft_ms": 0,
                            "gpu_util": 0, "gpu_mem_pct": 0}, "score": 0}
    cur["score"] = _score(cur["metrics"], goal)
    best = cur

    spec_on = current.get("spec-type") != "off"
    tune_params = ["batch-size", "ubatch-size", "threads"]
    if spec_on:
        tune_params += ["spec-draft-n-max", "spec-draft-n-min"]

    _append_log(job_id, f"[Fase 2 fine] discesa per coordinate, regolo {tune_params}")
    for param in tune_params:
        options = CONTINUOUS_GRID.get(param, [])
        improved = True
        while improved:
            improved = False
            cur_val = int(best["config"].get(param, options[0]))
            idx = options.index(cur_val) if cur_val in options else 0
            # Si prova un passo per ciascun lato
            for ni in (idx - 1, idx + 1):
                if ni < 0 or ni >= len(options):
                    continue
                trial = dict(best["config"])
                trial[param] = str(options[ni])
                if not _fits_vram(trial, model_size_gb, ctx_size, gpu_vram_gb, _get_calib(job_id)):
                    continue
                r = _run_one(executor, target, engine, model_path, trial, ctx_size,
                             job_id, f"fine {param}={options[ni]}")
                if r is None:
                    continue
                r["score"] = _score(r["metrics"], goal)
                if r["score"] > best["score"]:
                    best = r
                    improved = True
                    _append_log(job_id, f"    ✓ Miglioramento: {param}={options[ni]} punteggio→{r['score']}")
                    break
    _append_log(job_id, f"  Convergenza fine: {best['label']} (punteggio {best['score']})")
    return best


def _alt_engines(executor: Executor, target: Target) -> list:
    """[2026-10-02 v1.1.27] Altre build di llama-server installate che usano la GPU (rocm / vulkan / cuda), diverse da quella in uso.
    Su una Radeon e' tipicamente la coppia Vulkan <-> ROCm. Le build solo CPU o di backend non riconosciuto sono escluse."""
    from . import installer
    cur = (target.engine_path or "").replace("/", "\\").lower()
    out = []
    for b in installer.find_llama_installs(executor, target):
        be = (b.get("backend") or "").lower().rstrip("?")
        if b["path"].replace("/", "\\").lower() == cur:
            continue
        if any(k in be for k in ("rocm", "vulkan", "cuda")):
            out.append(b)
    return out


def _try_other_engines(executor, target, model_path, ctx_size, goal, job_id, best: dict) -> List[dict]:
    """Rimisura la config migliore con ogni altra build GPU. Restituisce i risultati riusciti (con campo engine e label con il backend).
    Se la build non supporta qualche parametro (es. draft-mtp) l'avvio fallisce e la build viene saltata con una nota nel log."""
    from dataclasses import replace
    alts = _alt_engines(executor, target)
    if not alts:
        _append_log(job_id, "[Motori] nessun'altra build GPU installata da confrontare")
        return []
    _append_log(job_id, f"[Motori] confronto con {len(alts)} altra/e build: " + ", ".join(a.get("backend", "?") for a in alts))
    with _LOCK:
        _done = _JOBS.get(job_id, {}).get("progress", {}).get("done", 0)
    _set_progress(job_id, total=_done + len(alts))
    out = []
    for a in alts:
        alt_target = replace(target, engine_path=a["path"])
        eng = LlamaCppAdapter(executor, alt_target)
        r = _run_one(executor, alt_target, eng, model_path, best["config"], ctx_size, job_id, f"motore {a.get('backend', '?')}")
        if not r:
            _append_log(job_id, f"  [motore {a.get('backend', '?')}] non utilizzabile con questa configurazione: saltato")
            continue
        r["score"] = _score(r["metrics"], goal)
        r["engine"] = {"backend": a.get("backend", ""), "version": a.get("version", ""), "path": a["path"]}
        r["label"] = f"{r['label']} @ {a.get('backend', '?')}"
        out.append(r)
    return out


# ==================== Flusso principale ====================

def start_tune(target_id: str, model: str, ctx_size: int = 8192,
               goal: str = "latency", baseline_cfg: Optional[dict] = None,
               model_size_gb: float = 0.0, try_engines: bool = False) -> dict:
    """Avvia il task di tuning in due fasi.
    [2026-10-02 v1.1.27] try_engines: alla fine rimisura la configurazione migliore con le altre build GPU installate (es. ROCm vs Vulkan).
    baseline_cfg: parametri originali dell'utente (dict), misurati per primi come gruppo di confronto di baseline.
    [2026-10-01 v1.1.11] None = nessuna baseline; {} = baseline con i parametri predefiniti del motore.
    model_size_gb: dimensione del modello, per la pre-verifica della VRAM; se omessa vale 0 e la pre-verifica viene saltata.
    """
    target = get_target(target_id)
    if not target:
        return {"ok": False, "message": "Macchina target inesistente"}
    if not target.engine_path:
        return {"ok": False, "message": "Motore di inferenza non configurato, installarlo prima nelle Impostazioni"}
    if getattr(target, "engine_type", "llama_cpp") != "llama_cpp":
        return {"ok": False, "message": "Il tuning automatico per ora supporta solo il motore llama.cpp (il sistema di parametri di vLLM e' diverso, non ancora supportato)"}
    if not target.models_dir or not model:
        return {"ok": False, "message": "Modello non selezionato o cartella dei modelli vuota"}
    if ctx_size < 1024:
        return {"ok": False, "message": "ctx-size troppo piccolo, almeno 1024"}

    job_id = uuid.uuid4().hex[:8]
    with _LOCK:
        _JOBS[job_id] = {
            "job_id": job_id, "target_id": target_id, "model": model,
            "ctx_size": ctx_size, "goal": goal,
            "status": "running", "logs": [], "results": [],
            "baseline": None, "best": None, "error": "",
            "ts_start": time.time(), "meta": {"target_name": target.name, "os": target.os, "try_engines": try_engines},   # v1.1.24: per lo storico
        }

    def _worker():
        nonlocal model_size_gb
        executor = None
        try:
            from .executor import make_executor
            from .collectors import path_join
            executor = make_executor(target)
            engine = LlamaCppAdapter(executor, target)

            if not engine.check_installed():
                _fail(job_id, "Motore di inferenza non rilevato sulla macchina target, installarlo prima con un clic")
                _append_log(job_id, "✗ Motore di inferenza non rilevato")
                return

            model_path = path_join(target, target.models_dir, model)

            # Ricava la VRAM della macchina target; se la dimensione del modello non e' passata la rileva automaticamente (per la pre-verifica della VRAM)
            gpu_vram_gb = _get_gpu_vram(executor, target)
            if model_size_gb <= 0:
                model_size_gb = _get_model_size_gb(executor, target, model_path)
            _append_log(job_id, f"VRAM della macchina target: {gpu_vram_gb:.1f} GB | modello: {model} "
                                f"({model_size_gb:.1f} GB) | ctx fisso {ctx_size} | obiettivo: "
                                f"{GOAL_LABELS.get(goal, goal)}")
            # [2026-10-02 v1.1.24] metadati per lo storico: motore (backend + versione), GPU, dimensione modello
            try:
                from . import installer, collectors
                ei = installer.detect_engine(executor, target)
                gi = collectors._static_gpu_cached(executor, target, target.os) or {}
                with _LOCK:
                    _JOBS[job_id]["meta"].update({
                        "engine": {"type": target.engine_type, "backend": ei.get("backend", ""),
                                   "version": ei.get("version", ""), "path": ei.get("path", "")},
                        "gpu": {"name": gi.get("name", ""), "vram_gb": round(gpu_vram_gb, 1)},
                        "model_size_gb": round(model_size_gb, 1)})
            except Exception:
                pass
            all_results = []

            # Baseline: prima si misurano i parametri originali dell'utente
            # [2026-10-01 v1.1.12] Il tuning riavvia llama-server a ogni prova: se il Deploy ne ha uno in esecuzione viene fermato
            if engine.is_running():
                _append_log(job_id, "llama-server in esecuzione (avviato dal Deploy): verra' fermato e riavviato con i parametri di test")
            # [2026-10-01 v1.1.11] Stato MTP (modello + build) e baseline sanificata.
            # Versione precedente: if baseline_cfg:  (baseline sempre quella fissa del frontend; {} = nessuna baseline)
            mtp = mtp_state(executor, target, model)
            _append_log(job_id, f"Supporto MTP: modello={'si' if mtp['model'] else 'no'} | "
                                f"build={'si' if mtp['build'] else ('no' if mtp['build'] is False else 'non verificabile')} "
                                f"-> {'draft-mtp proposto' if mtp['allowed'] else 'draft-mtp escluso'}")
            cfg_base = baseline_cfg
            if cfg_base is not None and not mtp["allowed"] and any(is_spec_key(k) for k in cfg_base):
                cfg_base = strip_mtp(cfg_base)
                _append_log(job_id, "  Baseline: parametri di decodifica speculativa (MTP) rimossi perche' non supportati")
            _set_progress(job_id, phase="baseline", total=(1 if cfg_base is not None else 0) + 10)
            if cfg_base is not None:
                _append_log(job_id, "[Baseline] test della tua configurazione attuale" if cfg_base
                            else "[Baseline] test con i parametri predefiniti del motore")
                b = _run_one(executor, target, engine, model_path, cfg_base,
                             ctx_size, job_id, "baseline")
                if b:
                    b["score"] = _score(b["metrics"], goal)
                    all_results.append(b)
                    with _LOCK:
                        _JOBS[job_id]["baseline"] = b
                    _append_log(job_id, f"  Punteggio baseline: {b['score']}")

            # Fase uno coarse
            coarse_best = _coarse_search(executor, target, engine, model_path,
                                         ctx_size, model_size_gb, gpu_vram_gb,
                                         goal, job_id, allow_mtp=mtp["allowed"],
                                         baseline_result=all_results[0] if all_results else None)
            if not coarse_best:
                _fail(job_id, "Nessuna configurazione utilizzabile nella fase coarse (forse VRAM insufficiente)")
                with _LOCK:
                    _JOBS[job_id]["results"] = all_results
                return
            all_results.append(coarse_best)

            # Fase due fine
            fine_best = _fine_search(executor, target, engine, model_path,
                                     ctx_size, model_size_gb, gpu_vram_gb,
                                     goal, job_id, coarse_best["config"])
            all_results.append(fine_best)

            # Raccomandazione finale = risultato di convergenza della fase fine; confronto con la baseline
            # [2026-10-02 v1.1.25] Raccomandazione = la MIGLIORE tra tutte le misure (baseline, coarse, fine), non l'ultima fase.
            # Prima: final_best = fine_best if fine_best["score"] > 0 else coarse_best  -> poteva consigliare una configurazione
            # PEGGIORE della baseline (68.23 -> 65.5 t/s, -4%) perche' la fase fine rimisura la stessa riga con rumore.
            # Una variante sostituisce la tua configurazione solo se la supera di almeno il margine di rumore (3%).
            NOISE = 1.03
            pool = [r for r in all_results if r.get("score", 0) > 0]
            final_best = max(pool, key=lambda r: r["score"]) if pool else coarse_best
            base_r = all_results[0] if all_results and all_results[0] is _JOBS[job_id].get("baseline") else None
            if base_r and final_best is not base_r and final_best["score"] < base_r["score"] * NOISE:
                final_best = base_r
                _append_log(job_id, "  Nessuna variante supera la tua configurazione attuale oltre il margine di rumore (3%): resta consigliata quella.")
            # [2026-10-02 v1.1.27] Prova degli altri motori (Vulkan <-> ROCm...): tutte le misure fatte finora sono del motore in uso
            cur_eng = _JOBS[job_id].get("meta", {}).get("engine", {})
            for r_ in all_results:
                r_.setdefault("engine", cur_eng)
            if try_engines:
                alt_res = _try_other_engines(executor, target, model_path, ctx_size, goal, job_id, final_best)
                all_results.extend(alt_res)
                for r_ in alt_res:
                    # un altro motore sostituisce la scelta solo se supera la migliore di almeno il 3% (margine di rumore)
                    if r_["score"] > final_best["score"] * NOISE:
                        final_best = r_
                        _append_log(job_id, f"  ✓ Il motore {r_['engine']['backend']} e' piu' veloce: consigliato il cambio di motore")
            _finalize(job_id, all_results, final_best)
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


def _get_model_size_gb(executor: Executor, target: Target, model_path: str) -> float:
    """Rileva la dimensione reale (GB) del file del modello sulla macchina target, per la pre-verifica della VRAM

    [2026-10-01 v1.1.8] Su Windows la dimensione risultava 0.0 GB: il comando originale (if(Test-Path)...) e' fragile
    con le virgolette passate da cmd. Ora: Get-Item -LiteralPath, e in caso di fallimento ripiego su cmd (%~z).
    # Versione precedente (2026-10-01, sostituita):
    # cmd = (f'powershell -Command "if(Test-Path \\'{model_path}\\')'
    #        f'{(chr(123))}(Get-Item \\'{model_path}\\').Length{(chr(125))}else{{0}}"')
    """
    if target.os == "windows":
        cmds = [f'powershell -NoProfile -Command "(Get-Item -LiteralPath \'{model_path}\').Length"',
                f'for %I in ("{model_path}") do @echo %~zI']
    else:
        cmds = [f'stat -c %s "{model_path}" 2>/dev/null || echo 0']
    for cmd in cmds:
        r = executor.run(cmd, timeout=15)
        tok = (r.stdout or "").split()
        digits = "".join(c for c in (tok[-1] if tok else "") if c.isdigit())
        if digits and int(digits) > 0:
            return round(int(digits) / (1024 ** 3), 1)
    return 0.0


def _log_server_tail(executor: Executor, target: Target, righe: int = 12) -> list:
    """[2026-10-01 v1.1.8] Ultime righe del log di llama-server (Windows: C:\\temp\\llama_server.log, Linux: /tmp/llama_server.log),
    per capire PERCHE' l'avvio e' fallito (parametro non supportato, VRAM, DLL mancanti...)."""
    if target.os == "windows":
        cmd = f'powershell -NoProfile -Command "Get-Content -Tail {righe} C:\\temp\\llama_server.log"'
    else:
        cmd = f"tail -n {righe} /tmp/llama_server.log 2>/dev/null"
    r = executor.run(cmd, timeout=10)
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]


def _get_gpu_vram(executor: Executor, target: Target) -> float:
    try:
        from .collectors import _detect_gpu_static, _detect_memory_static
        gpu = _detect_gpu_static(executor, target)
        if gpu:
            v = gpu.get("total_memory_gb", 0)
            if v > 0:
                return float(v)
            if gpu.get("unified"):
                mem = _detect_memory_static(executor, target)
                return mem.get("total_gb", 0) * 0.75
    except Exception:
        pass
    return 0.0


def _fail(job_id: str, err: str):
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job["status"] = "failed"
            job["error"] = err
            _snap = dict(job)
        else:
            _snap = None
    # [2026-10-02 v1.1.24] ogni tuning concluso (anche fallito) entra nello storico (tune_log)
    if _snap:
        from .tune_log import add_entry
        add_entry(_snap)


def _finalize(job_id: str, results: List[dict], best: dict):
    # Segna la raccomandazione
    # [2026-10-02 v1.1.25] una sola riga consigliata (prima: ogni riga con la stessa etichetta, anche ripetuta)
    for r in results:
        r["recommended"] = (r is best)
    if not any(r.get("recommended") for r in results):
        best["recommended"] = True
    with _LOCK:
        job = _JOBS[job_id]
        job["status"] = "success"
        job["results"] = results
        job["best"] = best
        _tid, _model, _ctx = job["target_id"], job["model"], job["ctx_size"]
        _snap = dict(job)
    from .tune_log import add_entry          # [2026-10-02 v1.1.24] storico completo delle ottimizzazioni
    add_entry(_snap)
    _append_log(job_id, f"✓ Tuning completato, consigliata: {best['label']} (punteggio {best['score']})")
    # Salva su disco gli ultimi parametri di tuning, per riempire i default della pagina Deploy
    try:
        from .tune_history import save_latest
        save_latest(_tid, _model, _ctx, best.get("config", {}),
                    source="tuner", score=best.get("metrics", {}).get("decode", 0))
        # [2026-10-02 v1.1.26] Il Deploy mostra questo numero come «misurati X t/s»: si salva la DECODIFICA in t/s, non il punteggio
        # composito (decodifica + prefill/100 + 1/TTFT) che dava valori come 42.61 «t/s» diversi da quelli reali.
        # Versione precedente: score=best.get("score", 0)
    except Exception as e:
        _append_log(job_id, f"  ⚠ Salvataggio su disco del risultato di tuning non riuscito: {e}")
