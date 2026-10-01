"""Generatore deterministico di configurazione

In base alle specifiche hardware + alle informazioni sul file del modello, genera con pura logica di calcolo un insieme di parametri di base «necessariamente corretti».
Non dipende da un LLM, non richiede seed, e' generico per i nuovi utenti.

Ogni regola ha una base di calcolo esplicita:
  1. Pesi del modello < VRAM x 0.85 -> ngl=all (scarico completo sulla GPU)
  2. La VRAM residua determina il livello di quantizzazione della KV cache (prima alta precisione, si scende solo se non ci sta)
  3. Il modello supporta MTP -> abilita la decodifica speculativa + strati draft tutti in GPU
  4. La VRAM residua determina il limite del batch
  5. Numero di thread = core fisici della CPU (al massimo 32)

Principi di progetto:
  - Meglio prudenti che OOM: tutte le stime lasciano un margine del 15%
  - Ogni regola e' testabile in modo indipendente
  - L'output e' utilizzabile direttamente come parametri di avvio di llama-server
"""

import re
from typing import Optional

# Occupazione di VRAM della KV cache per token per strato (byte), per livello di quantizzazione
# Formula approssimata: 2(K+V) x head_dim x bytes_per_element
# Per modelli da 27B (head_dim~128, 64 strati): circa 2x128x64xbytes = 16384xbytes per token
_KV_BYTES_PER_TOKEN_PER_LAYER = {
    "f16": 2.0,    # 2 bytes per element
    "q8_0": 1.0,   # 1 byte
    "q4_0": 0.5,   # 0.5 byte
}

# Stima del costo extra di VRAM del modello draft per la decodifica speculativa (GB)
# Il draft MTP di solito e' 1-2 strati del modello principale, circa 0.3-0.8GB
_DRAFT_OVERHEAD_GB = 0.5

# Margine di sicurezza della VRAM (lasciato a contesto CUDA, frammentazione, attivazioni)
_VRAM_HEADROOM = 0.85  # usa solo l'85% della VRAM


def generate_config(
    gpu_vram_gb: float,
    model_size_gb: float,
    model_filename: str,
    ctx_size: int,
    cpu_cores: int = 8,
    cpu_threads: int = 16,
    num_layers: int = 0,
) -> dict:
    """Genera la configurazione di base deterministica.

    Args:
        gpu_vram_gb: VRAM totale della GPU (GB)
        model_size_gb: dimensione del file del modello (GB)
        model_filename: nome del file del modello (per dedurre tipo di quantizzazione e supporto MTP)
        ctx_size: lunghezza di contesto richiesta dall'utente
        cpu_cores: core fisici della CPU
        cpu_threads: thread logici della CPU
        num_layers: numero di strati del modello (0 = deduzione automatica)

    Returns:
        {
            "params": {...},       # parametri di llama-server
            "reasoning": [...],    # motivazione della decisione di ogni regola (per AI e utente)
            "warnings": [...],     # avvisi su rischi potenziali
        }
    """
    reasoning = []
    warnings = []
    params = {}

    # ========== Regola 0: deduzione del numero di strati del modello ==========
    if num_layers <= 0:
        num_layers = _infer_layers(model_filename, model_size_gb)
        reasoning.append(f"Numero di strati del modello dedotto: {num_layers} (in base a nome del file e dimensione)")

    # ========== Regola 1: strategia di scarico sulla GPU ==========
    usable_vram = gpu_vram_gb * _VRAM_HEADROOM
    model_fits = model_size_gb <= usable_vram

    if model_fits:
        params["n-gpu-layers"] = "all"
        reasoning.append(
            f"Modello {model_size_gb:.1f}GB < VRAM utilizzabile {usable_vram:.1f}GB "
            f"({gpu_vram_gb}x{_VRAM_HEADROOM}) -> scarico completo sulla GPU"
        )
    else:
        # Il modello non ci sta: calcola quanti strati ci stanno
        layers_fit = int((usable_vram / model_size_gb) * num_layers * 0.9)
        params["n-gpu-layers"] = str(max(layers_fit, 1))
        warnings.append(
            f"Modello {model_size_gb:.1f}GB oltre la VRAM utilizzabile {usable_vram:.1f}GB, "
            f"si possono scaricare sulla GPU solo {layers_fit}/{num_layers} strati, le prestazioni calano sensibilmente"
        )
        reasoning.append(
            f"Il modello non ci sta -> scarico parziale di {layers_fit} strati (e' l'unico caso in cui e' ammesso un valore diverso da all)"
        )

    # ========== Regole 2+3: quantizzazione KV cache + decodifica speculativa (decisione congiunta) ==========
    # Principio base: il vantaggio della decodifica speculativa (+50~100%) e' molto maggiore della differenza di precisione della cache (<5%),
    # quindi si garantisce prima la decodifica speculativa e, se serve, si abbassa la quantizzazione della cache per liberare VRAM.
    remaining_vram = usable_vram - model_size_gb
    supports_mtp = _supports_mtp(model_filename)

    if supports_mtp and model_fits:
        # Cerca dal livello piu' alto al piu' basso quello che «puo' contenere insieme KV + draft + margine per il batch»
        cache_type = None
        for ct in ["f16", "q8_0", "q4_0"]:
            kv_gb = _estimate_kv_gb(ctx_size, num_layers, ct)
            after = remaining_vram - kv_gb - _DRAFT_OVERHEAD_GB
            if after > 1.0:  # lascia almeno 1GB per batch/attivazioni
                cache_type = ct
                break
        if cache_type is None:
            # Se nemmeno q4_0 contiene il draft -> rinuncia alla speculativa, usa la cache a massima precisione
            cache_type = _choose_cache_type(remaining_vram, ctx_size, num_layers)
            warnings.append("Il margine di VRAM non basta a contenere anche il modello draft, decodifica speculativa saltata")
            reasoning.append("VRAM insufficiente per abilitare la decodifica speculativa, ripiego su schema senza speculativa")
        else:
            params["spec-type"] = "draft-mtp"
            params["spec-draft-n-max"] = "3"
            params["gpu-layers-draft"] = "all"
            params["spec-draft-ngl"] = "all"
            kv_usage = _estimate_kv_gb(ctx_size, num_layers, cache_type)
            reasoning.append(
                f"Il modello supporta MTP, per garantire la decodifica speculativa si sceglie cache={cache_type}"
                f"(KV occupa {kv_usage:.1f}GB + draft {_DRAFT_OVERHEAD_GB}GB, "
                f"restano {remaining_vram - kv_usage - _DRAFT_OVERHEAD_GB:.1f}GB)"
            )
    else:
        # MTP non supportato o modello che non ci sta -> si sceglie solo la cache a massima precisione
        cache_type = _choose_cache_type(remaining_vram, ctx_size, num_layers)
        kv_usage = _estimate_kv_gb(ctx_size, num_layers, cache_type)
        if not supports_mtp:
            reasoning.append("Il modello non supporta la decodifica speculativa MTP (nel nome del file non e' stato rilevato alcun marcatore)")

    params["cache-type-k"] = cache_type
    params["cache-type-v"] = cache_type
    kv_usage = _estimate_kv_gb(ctx_size, num_layers, cache_type)
    reasoning.append(
        f"VRAM residua {remaining_vram:.1f}GB, ctx={ctx_size}, "
        f"KV cache({cache_type}) occupa circa {kv_usage:.1f}GB"
    )

    # ========== Regola 4: dimensione del batch ==========
    # Il batch influisce soprattutto sulla velocita' di prefill, poco sulla fase di decodifica
    # Regola empirica: con margine di VRAM > 4GB si usa 4096, > 2GB 2048, altrimenti 1024
    after_all = remaining_vram - kv_usage
    if supports_mtp and model_fits:
        after_all -= _DRAFT_OVERHEAD_GB

    if after_all > 4.0:
        batch, ubatch = 4096, 1024
    elif after_all > 2.0:
        batch, ubatch = 2048, 512
    else:
        batch, ubatch = 1024, 256
    params["batch-size"] = str(batch)
    params["ubatch-size"] = str(ubatch)
    reasoning.append(
        f"Dopo aver detratto modello+KV+draft restano {after_all:.1f}GB -> batch={batch}, ubatch={ubatch}"
    )

    # ========== Regola 5: numero di thread ==========
    # Thread = core fisici, ma al massimo 32 (oltre i benefici decrescono)
    threads = min(cpu_cores, 32)
    params["threads"] = str(threads)
    reasoning.append(f"CPU {cpu_cores} core -> threads={threads}")

    # ========== Parametri fissi ==========
    params["flash-attn"] = "on"
    params["fit"] = "off"
    params["ctx-size"] = str(ctx_size)

    return {
        "params": params,
        "reasoning": reasoning,
        "warnings": warnings,
    }


# ==================== Funzioni di calcolo interne ====================

def _infer_layers(filename: str, size_gb: float) -> int:
    """Deduce il numero di strati del modello da nome del file e dimensione"""
    # Mappatura comune parametri del modello -> strati
    name_lower = filename.lower()
    if "70b" in name_lower:
        return 80
    elif "32b" in name_lower or "34b" in name_lower:
        return 64
    elif "27b" in name_lower:
        return 64
    elif "14b" in name_lower:
        return 40
    elif "8b" in name_lower or "7b" in name_lower:
        return 32
    elif "3b" in name_lower or "4b" in name_lower:
        return 36
    elif "1b" in name_lower or "0.5b" in name_lower or "0.6b" in name_lower:
        return 24
    # Stima approssimativa in base alla dimensione: circa 0.2-0.4GB per strato (dipende dalla quantizzazione)
    return max(int(size_gb / 0.25), 16)


def _supports_mtp(filename: str) -> bool:
    """Stabilisce se il modello supporta la decodifica speculativa MTP.
    La serie Qwen3 (incluse 3.5/3.6/3.7/3.8) supporta MTP in modo nativo.
    Gli altri modelli richiedono un marcatore esplicito nel nome del file."""
    name_lower = filename.lower()
    # Tutta la serie Qwen3 supporta MTP
    if re.search(r"qwen3[\.\-]?\d", name_lower):
        return True
    if "qwen3" in name_lower:
        return True
    # Marcatore esplicito
    if "mtp" in name_lower:
        return True
    return False


def _estimate_kv_gb(ctx_size: int, num_layers: int, cache_type: str) -> float:
    """Stima l'occupazione di VRAM della KV cache (GB)
    Formula: 2(K+V) x ctx x head_dim x layers x bytes_per_element
    head_dim fissato a 128 (modelli principali)"""
    head_dim = 128
    bytes_per_elem = _KV_BYTES_PER_TOKEN_PER_LAYER.get(cache_type, 2.0)
    total_bytes = 2 * ctx_size * head_dim * num_layers * bytes_per_elem
    return total_bytes / (1024 ** 3)


def _choose_cache_type(remaining_vram: float, ctx_size: int, num_layers: int) -> str:
    """Sceglie il livello di quantizzazione della cache a precisione piu' alta in base alla VRAM residua.
    Prima f16 (il piu' veloce), se non ci sta si scende a q8_0, poi q4_0."""
    for cache_type in ["f16", "q8_0", "q4_0"]:
        kv_gb = _estimate_kv_gb(ctx_size, num_layers, cache_type)
        if kv_gb <= remaining_vram * 0.8:  # lascia il 20% di margine per il batch
            return cache_type
    # Non ci sta niente, si usa il piu' piccolo
    return "q4_0"
