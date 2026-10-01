"""Registro / factory dei motori di inferenza

Gestisce in modo centralizzato i metadati di tutti i motori disponibili e l'istanziazione degli adattatori. Il chiamante (deploy / installer /
pagina di configurazione del frontend) deve solo ottenere adattatore o metadati in base a target.engine_type, senza piu' classi concrete cablate.

Passi per aggiungere un motore:
  1. Implementare una sottoclasse di EngineAdapter (es. vllm.VLLMAdapter)
  2. Aggiungere una voce in _ADAPTERS e una in ENGINE_META qui sotto
"""

from typing import Optional

from .engine_adapter import EngineAdapter, StartParams  # noqa: F401  (re-export)
from .llama_cpp import LlamaCppAdapter
from .vllm import VLLMAdapter
from .sglang import SGLangAdapter
from .comfyui import ComfyUIAdapter
from ..models.target import Target

# engine_type -> classe dell'adattatore
_ADAPTERS = {
    "llama_cpp": LlamaCppAdapter,
    "vllm": VLLMAdapter,
    "sglang": SGLangAdapter,
    "comfyui": ComfyUIAdapter,
}

# engine_type -> metadati di visualizzazione e capacita' (usati dalle pagine di configurazione/deploy del frontend)
# Nota: i testi rivolti all'utente come desc / install_hint / note / windows_note sono ora presi dal frontend nella lingua dell'interfaccia
# tramite le chiavi engine.<type>.<field> di frontend/src/i18n/translations.js (vedi engineText in Settings.jsx);
# qui si conserva lo stesso testo come valore predefinito dell'API e ripiego: se si cambia un testo, aggiornare anche l'i18n del frontend, altrimenti nell'interfaccia non si vede la modifica.
ENGINE_META = {
    "llama_cpp": {
        "label": "llama.cpp",
        "desc": "Nativo multipiattaforma, supporta modelli quantizzati GGUF, inferenza ibrida CPU/GPU, prima scelta per l'utente comune",
        "supported_os": ["windows", "linux", "macos"],
        "model_format": "gguf",
        "install_hint": "Installazione con un clic del pacchetto precompilato ufficiale / brew / compilazione dai sorgenti",
        "default_cmd": "llama-server",
    },
    "vllm": {
        "label": "vLLM",
        "desc": "Motore di inferenza ad alto throughput, richiede GPU NVIDIA + CUDA, usa pesi HuggingFace safetensors",
        "supported_os": ["linux", "macos"],
        "model_format": "safetensors",
        "install_hint": "pip install vllm (richiede Python 3.9+ e ambiente CUDA)",
        "default_cmd": "vllm",
        # vLLM non supporta Windows in modo nativo, il frontend lo usa per suggerire WSL2
        "windows_note": "vLLM non supporta l'esecuzione nativa su Windows: distribuirlo in WSL2 (Linux) oppure usare llama.cpp",
    },
    "sglang": {
        "label": "SGLang",
        "desc": "Framework di inferenza ad alto throughput (cache dei prefissi RadixAttention, parallelismo multi-GPU), richiede GPU NVIDIA + CUDA, usa pesi HuggingFace",
        "supported_os": ["linux", "macos"],
        "model_format": "safetensors",
        "install_hint": "Installare sglang con pip/uv (richiede Python 3.10+ e ambiente CUDA)",
        "default_cmd": "sglang",
        # Le istruzioni ufficiali di installazione di SGLang riguardano Linux + GPU NVIDIA
        "windows_note": "Le istruzioni ufficiali di installazione di SGLang riguardano Linux + GPU NVIDIA: distribuirlo in WSL2 (Linux) oppure usare llama.cpp",
    },
    "comfyui": {
        "label": "ComfyUI",
        "desc": "Motore a nodi per la generazione di immagini/video, distribuisce in locale modelli video open source (Wan2.1 / CogVideoX / LTX-Video ecc.), richiede GPU NVIDIA e molta VRAM",
        "supported_os": ["windows", "linux", "macos"],
        "model_format": "safetensors",
        "task": "video",
        "install_hint": "git clone di ComfyUI + pip install -r requirements.txt (richiede Python 3.10+ e CUDA)",
        "default_cmd": "python main.py",
        "default_port": 8188,
        "note": "I modelli di generazione video richiedono molta VRAM (6~24GB nelle versioni quantizzate): filtrare nel negozio i modelli eseguibili in base alla VRAM della macchina",
    },
}


def get_adapter(executor, target: Target) -> EngineAdapter:
    """Restituisce l'istanza dell'adattatore del motore corrispondente a target.engine_type"""
    cls = _ADAPTERS.get(target.engine_type, LlamaCppAdapter)
    return cls(executor, target)


def get_meta(engine_type: str) -> Optional[dict]:
    """Restituisce i metadati del motore; per un tipo sconosciuto restituisce None"""
    return ENGINE_META.get(engine_type)


def is_supported_on(engine_type: str, os_name: str) -> bool:
    """Il motore supporta il sistema operativo di destinazione indicato?"""
    meta = ENGINE_META.get(engine_type)
    if not meta:
        return False
    return os_name in meta["supported_os"]


def list_engines() -> list:
    """Elenca tutti i motori (con il campo type), per il rendering di menu/schede nel frontend"""
    out = []
    for et, meta in ENGINE_META.items():
        out.append({"type": et, **meta})
    return out
