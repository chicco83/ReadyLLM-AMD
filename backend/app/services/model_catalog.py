"""Servizio del catalogo dei modelli

Supporta due modalita':
  1. Catalogo integrato selezionato (cablato nel codice, garantisce l'uso offline)
  2. Recupero dinamico (scarica da HuggingFace API i modelli GGUF popolari, con aggiornamento manuale)

Ogni voce fornisce informazioni di download da piu' sorgenti:
  - huggingface: sito originale (estero, puo' essere lento/non raggiungibile)
  - hf-mirror: mirror di HuggingFace (adatto alla Cina continentale, percorsi identici a HF, predefinito)
  - modelscope: comunita' ModelScope (la piu' veloce in Cina continentale, richiede che il modello abbia un ms_repo corrispondente, altrimenti ripiega sul mirror)

Nota: le dimensioni dei modelli sono approssimative, servono solo per la visualizzazione e il filtro per VRAM; fa fede il repository.
"""

import json
import subprocess
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from typing import List, Optional

HF_BASE = "https://huggingface.co"
HF_MIRROR_BASE = "https://hf-mirror.com"
MS_BASE = "https://modelscope.cn/models"

# Priorita' delle sorgenti: mirror predefinito (il piu' stabile, percorsi identici a HF)
DEFAULT_SOURCE = "hf-mirror"
SOURCE_LABELS = {
    "huggingface": "HuggingFace",
    "hf-mirror": "Mirror HF",
    "modelscope": "ModelScope",
}


@dataclass
class ModelEntry:
    id: str
    name: str            # nome visualizzato, es. "Qwen3-8B"
    quant: str           # quantizzazione, es. Q4_K_M
    repo: str            # repo HuggingFace, es. "bartowski/Qwen2.5-8B-Instruct-GGUF"
    filename: str        # nome del file, es. "Qwen2.5-8B-Instruct-Q4_K_M.gguf"
    size_gb: float       # dimensione approssimativa
    min_vram_gb: int     # VRAM minima consigliata
    desc: str = ""
    tags: List[str] = field(default_factory=list)
    ms_repo: str = ""    # repository ModelScope (es. "Qwen/Qwen3-8B-GGUF"), se vuoto il modello non supporta la sorgente ModelScope
    category: str = "text"  # categoria del modello: text=inferenza testuale (llama.cpp/vLLM), video=generazione video (ComfyUI)
    engine: str = ""        # motore consigliato: se vuoto si deduce dalla category (text->llama_cpp, video->comfyui)

    def hf_url(self, base: str = HF_BASE) -> str:
        return f"{base}/{self.repo}/resolve/main/{self.filename}"

    def ms_url(self) -> str:
        # Link diretto GGUF di ModelScope: resolve/master, in alcuni repository si usa il nome del file
        return f"{MS_BASE}/{self.ms_repo}/resolve/master/{self.filename}"

    def resolve(self, source: str = DEFAULT_SOURCE) -> tuple:
        """Risolve il link di download diretto in base alla sorgente, restituisce (url, sorgente effettivamente usata).
        Se ModelScope non ha il repository corrispondente ripiega sul mirror."""
        if source == "modelscope" and self.ms_repo:
            return self.ms_url(), "modelscope"
        if source == "huggingface":
            return self.hf_url(HF_BASE), "huggingface"
        # hf-mirror, oppure ripiego quando modelscope non e' disponibile
        return self.hf_url(HF_MIRROR_BASE), "hf-mirror"

    def available_sources(self) -> List[str]:
        srcs = ["hf-mirror", "huggingface"]
        if self.ms_repo:
            srcs.insert(0, "modelscope")
        return srcs

    def to_dict(self, source: str = DEFAULT_SOURCE) -> dict:
        d = asdict(self)
        url, used = self.resolve(source)
        d["download_url"] = url
        d["used_source"] = used
        d["available_sources"] = self.available_sources()
        return d


# ==================== Modelli selezionati (piu' recenti 2025-2026) ====================

CATALOG: List[ModelEntry] = [
    # --- Serie Qwen3.8 (ultimo modello di punta, MTP nativo con decodifica speculativa) ---
    ModelEntry("qwen38-27b", "Qwen3.8-27B", "IQ4_NL",
               "bartowski/Qwen3.8-27B-GGUF", "Qwen3.8-27B-IQ4_NL.gguf",
               15.2, 20, "Ultimo modello di punta, MTP nativo con decodifica speculativa, fluido con 24G di VRAM", ["qwen3.8", "mtp", "large"],
               ms_repo="Qwen/Qwen3.8-27B-GGUF"),
    ModelEntry("qwen38-27b-q4km", "Qwen3.8-27B", "Q4_K_M",
               "bartowski/Qwen3.8-27B-GGUF", "Qwen3.8-27B-Q4_K_M.gguf",
               16.5, 22, "Quantizzazione standard del modello di punta, buona compatibilita'", ["qwen3.8", "mtp", "large"],
               ms_repo="Qwen/Qwen3.8-27B-GGUF"),
    ModelEntry("qwen38-8b", "Qwen3.8-8B", "Q4_K_M",
               "bartowski/Qwen3.8-8B-GGUF", "Qwen3.8-8B-Q4_K_M.gguf",
               5.2, 8, "Ultimo piccolo modello di punta, accelerazione MTP, fluido con 8G di VRAM", ["qwen3.8", "mtp"],
               ms_repo="Qwen/Qwen3.8-8B-GGUF"),

    # --- Serie Qwen3.5 ---
    ModelEntry("qwen35-32b", "Qwen3.5-32B", "Q4_K_M",
               "bartowski/Qwen3.5-32B-GGUF", "Qwen3.5-32B-Q4_K_M.gguf",
               19.5, 24, "Ragionamento forte, richiede 24G di VRAM", ["qwen3.5", "large"],
               ms_repo="Qwen/Qwen3.5-32B-GGUF"),
    ModelEntry("qwen35-14b", "Qwen3.5-14B", "Q4_K_M",
               "bartowski/Qwen3.5-14B-GGUF", "Qwen3.5-14B-Q4_K_M.gguf",
               9.2, 12, "Equilibrio tra capacita' e velocita'", ["qwen3.5"],
               ms_repo="Qwen/Qwen3.5-14B-GGUF"),
    ModelEntry("qwen35-8b", "Qwen3.5-8B", "Q4_K_M",
               "bartowski/Qwen3.5-8B-GGUF", "Qwen3.5-8B-Q4_K_M.gguf",
               5.0, 8, "Ottimo rapporto qualita'-prezzo", ["qwen3.5"],
               ms_repo="Qwen/Qwen3.5-8B-GGUF"),
    ModelEntry("qwen35-4b", "Qwen3.5-4B", "Q4_K_M",
               "bartowski/Qwen3.5-4B-GGUF", "Qwen3.5-4B-Q4_K_M.gguf",
               2.6, 4, "Leggero ed efficiente, gira anche su CPU", ["qwen3.5", "small"],
               ms_repo="Qwen/Qwen3.5-4B-GGUF"),

    # --- Serie Llama 4 ---
    ModelEntry("llama4-scout-17b", "Llama-4-Scout-17B", "Q4_K_M",
               "bartowski/Llama-4-Scout-17B-16E-Instruct-GGUF",
               "Llama-4-Scout-17B-16E-Instruct-Q4_K_M.gguf",
               11.0, 16, "Architettura MoE, 17B parametri attivi, multimodale", ["llama4", "moe"],
               ms_repo="LLM-Research/Llama-4-Scout-17B-16E-Instruct-GGUF"),
    ModelEntry("llama4-maverick-17b", "Llama-4-Maverick-17B", "Q4_K_M",
               "bartowski/Llama-4-Maverick-17B-128E-Instruct-GGUF",
               "Llama-4-Maverick-17B-128E-Instruct-Q4_K_M.gguf",
               65.0, 80, "Modello di punta MoE, 128 esperti, richiede molta VRAM", ["llama4", "moe", "large"],
               ms_repo="LLM-Research/Llama-4-Maverick-17B-128E-Instruct-GGUF"),

    # --- Serie DeepSeek ---
    ModelEntry("deepseek-r1-distill-32b", "DeepSeek-R1-Distill-32B", "Q4_K_M",
               "bartowski/DeepSeek-R1-Distill-Qwen-32B-GGUF",
               "DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf",
               19.5, 24, "Ragionamento potenziato, forte in matematica/codice", ["deepseek", "reasoning", "large"],
               ms_repo="deepseek-ai/DeepSeek-R1-Distill-Qwen-32B-GGUF"),
    ModelEntry("deepseek-r1-distill-14b", "DeepSeek-R1-Distill-14B", "Q4_K_M",
               "bartowski/DeepSeek-R1-Distill-Qwen-14B-GGUF",
               "DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
               9.0, 12, "Modello di ragionamento leggero", ["deepseek", "reasoning"],
               ms_repo="deepseek-ai/DeepSeek-R1-Distill-Qwen-14B-GGUF"),
    ModelEntry("deepseek-r1-distill-8b", "DeepSeek-R1-Distill-8B", "Q4_K_M",
               "bartowski/DeepSeek-R1-Distill-Llama-8B-GGUF",
               "DeepSeek-R1-Distill-Llama-8B-Q4_K_M.gguf",
               5.0, 8, "Modello di ragionamento di base", ["deepseek", "reasoning"],
               ms_repo="deepseek-ai/DeepSeek-R1-Distill-Llama-8B-GGUF"),

    # --- Serie Gemma 3 ---
    ModelEntry("gemma3-27b", "Gemma-3-27B", "Q4_K_M",
               "bartowski/gemma-3-27b-it-GGUF", "gemma-3-27b-it-Q4_K_M.gguf",
               16.5, 20, "Multimodale di Google, visione + testo", ["gemma3", "multimodal", "large"],
               ms_repo="google/gemma-3-27b-it-GGUF"),
    ModelEntry("gemma3-12b", "Gemma-3-12B", "Q4_K_M",
               "bartowski/gemma-3-12b-it-GGUF", "gemma-3-12b-it-Q4_K_M.gguf",
               7.5, 10, "Scelta equilibrata multimodale", ["gemma3", "multimodal"],
               ms_repo="google/gemma-3-12b-it-GGUF"),
    ModelEntry("gemma3-4b", "Gemma-3-4B", "Q4_K_M",
               "bartowski/gemma-3-4b-it-GGUF", "gemma-3-4b-it-Q4_K_M.gguf",
               2.8, 4, "Multimodale leggero", ["gemma3", "multimodal", "small"],
               ms_repo="google/gemma-3-4b-it-GGUF"),

    # --- Serie Mistral ---
    ModelEntry("mistral-small-3.2", "Mistral-Small-3.2-24B", "Q4_K_M",
               "bartowski/Mistral-Small-3.2-24B-Instruct-2506-GGUF",
               "Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf",
               14.0, 18, "Il miglior open source europeo, multilingue", ["mistral", "multilingual"],
               ms_repo="mistralai/Mistral-Small-3.2-24B-Instruct-2506-GGUF"),

    # --- Modelli per il codice ---
    ModelEntry("qwen3-coder-32b", "Qwen3-Coder-32B", "Q4_K_M",
               "bartowski/Qwen3-Coder-32B-GGUF", "Qwen3-Coder-32B-Q4_K_M.gguf",
               19.5, 24, "Il miglior modello open source per il codice", ["code", "qwen3", "large"],
               ms_repo="Qwen/Qwen3-Coder-32B-GGUF"),
    ModelEntry("qwen3-coder-8b", "Qwen3-Coder-8B", "Q4_K_M",
               "bartowski/Qwen3-Coder-8B-GGUF", "Qwen3-Coder-8B-Q4_K_M.gguf",
               5.0, 8, "Modello leggero per il codice", ["code", "qwen3"],
               ms_repo="Qwen/Qwen3-Coder-8B-GGUF"),

    # --- Embedding / Reranker ---
    ModelEntry("bge-m3", "bge-m3", "F16",
               "BAAI/bge-m3-GGUF", "bge-m3-F16.gguf",
               2.2, 4, "Modello di embedding multilingue, indispensabile per RAG", ["embedding"],
               ms_repo="BAAI/bge-m3-GGUF"),

    # ==================== Modelli di generazione video (ComfyUI / safetensors) ====================
    # Nota: i modelli video usano il motore ComfyUI, i pesi sono safetensors (non GGUF) e richiedono molta VRAM.
    # filename qui e' il nome del file dei pesi principali nella cartella checkpoints di ComfyUI, dopo il download va messo in
    # ComfyUI/models/checkpoints (per la verifica end-to-end dello step_7 si calibra sul nome file reale del repository).
    ModelEntry("wan21-t2v-1.3b", "Wan2.1-T2V-1.3B", "fp16",
               "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
               "split_files/v1/wan2.1_t2v_1.3b_fp16.safetensors",
               6.0, 8, "Text-to-video leggero, gira con 8G di VRAM, a partire da 480p",
               ["video", "wan", "t2v"], category="video", engine="comfyui"),
    ModelEntry("wan21-t2v-14b", "Wan2.1-T2V-14B", "fp16",
               "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
               "split_files/v1/wan2.1_t2v_14b_fp16.safetensors",
               28.0, 24, "Modello di punta text-to-video di alta qualita', richiede 24G+ di VRAM",
               ["video", "wan", "t2v", "large"], category="video", engine="comfyui"),
    ModelEntry("ltx-video-2b", "LTX-Video-2B", "fp16",
               "Lightricks/LTX-Video", "ltx-video-2b-v0.9.5.safetensors",
               2.5, 6, "Generazione velocissima, gira con 6G di VRAM, adatto alle anteprime rapide",
               ["video", "ltx", "fast"], category="video", engine="comfyui"),
    ModelEntry("cogvideox-5b", "CogVideoX-5B", "fp16",
               "zai-org/CogVideoX-5b", "CogVideoX-Fun-V1.1-5b-InP.safetensors",
               11.0, 16, "Modello video open source di Zhipu, 1280x720, richiede 16G di VRAM",
               ["video", "cogvideo"], category="video", engine="comfyui"),
]


# ==================== Recupero dinamico (HuggingFace API) ====================

# Si usa prima l'API del mirror (raggiungibile in Cina), in caso di errore si ripiega sul sito originale di HuggingFace
_HF_API_MIRROR = "https://hf-mirror.com/api/models"
_HF_API_ORIGIN = "https://huggingface.co/api/models"
_dynamic_cache: Optional[List[dict]] = None
_dynamic_cache_time: float = 0
_DYNAMIC_CACHE_TTL = 3600  # cache di 1 ora

# Prefissi dei repository popolari considerati nel recupero dinamico (modelli GGUF ordinati per download)
_DYNAMIC_SEARCH_TERMS = [
    "GGUF",
]
_DYNAMIC_LIMIT = 40  # numero massimo di elementi da scaricare


def fetch_dynamic_catalog(force: bool = False) -> dict:
    """Recupera dinamicamente da HuggingFace API i modelli GGUF popolari.
    Restituisce {"models": [...], "source": "dynamic", "updated_at": timestamp}
    In caso di errore restituisce {"models": [], "error": "..."}
    """
    global _dynamic_cache, _dynamic_cache_time

    # Usa la cache (se non e' un aggiornamento forzato e non e' scaduta)
    if not force and _dynamic_cache and (time.time() - _dynamic_cache_time) < _DYNAMIC_CACHE_TTL:
        return {"models": _dynamic_cache, "source": "dynamic",
                "updated_at": _dynamic_cache_time, "cached": True}

    try:
        # Cerca i repository di modelli GGUF popolari aggiornati di recente (prima il mirror, in caso di errore ripiega sul sito originale)
        # Nota: la libreria SSL del Python 3.9 di sistema potrebbe non riuscire a connettersi ad alcuni siti, si usa un sottoprocesso curl
        query = (f"?search=GGUF&sort=downloads&direction=-1"
                 f"&limit={_DYNAMIC_LIMIT}&filter=text-generation")
        data = None
        last_err = None
        for api_base in (_HF_API_MIRROR, _HF_API_ORIGIN):
            try:
                result = subprocess.run(
                    ["curl", "-s", "--max-time", "15", api_base + query],
                    capture_output=True, text=True, timeout=20,
                )
                if result.returncode == 0 and result.stdout.strip():
                    data = json.loads(result.stdout)
                    break
                else:
                    last_err = Exception(f"Codice di ritorno di curl {result.returncode}")
            except Exception as e:
                last_err = e
        if data is None:
            raise last_err or Exception("Tutte le sorgenti API sono irraggiungibili")

        models = []
        for item in data:
            repo_id = item.get("id", "")
            if not repo_id:
                continue
            # Conserva solo i repository GGUF
            if "gguf" not in repo_id.lower():
                continue
            downloads = item.get("downloads", 0)
            likes = item.get("likes", 0)
            updated = item.get("lastModified", "")
            # Estrae il nome del modello (toglie il suffisso -GGUF)
            name = repo_id.split("/")[-1].replace("-GGUF", "").replace("-gguf", "")
            models.append({
                "id": f"dyn-{repo_id.replace('/', '-')}",
                "name": name,
                "repo": repo_id,
                "downloads": downloads,
                "likes": likes,
                "updated_at": updated,
                "url": f"{HF_MIRROR_BASE}/{repo_id}",
                "source": "dynamic",
            })

        _dynamic_cache = models
        _dynamic_cache_time = time.time()
        return {"models": models, "source": "dynamic",
                "updated_at": _dynamic_cache_time, "cached": False}

    except Exception as e:
        # In caso di errore di rete restituisce la cache (se presente)
        if _dynamic_cache:
            return {"models": _dynamic_cache, "source": "dynamic",
                    "updated_at": _dynamic_cache_time, "cached": True,
                    "error": f"Aggiornamento non riuscito ({e}), mostro i dati in cache"}
        return {"models": [], "source": "dynamic", "error": str(e)}


# ==================== Interfaccia di interrogazione ====================

def list_all(source: str = DEFAULT_SOURCE, category: Optional[str] = None) -> List[dict]:
    """Elenca i modelli; category=None tutti, 'text'/'video' filtra per categoria"""
    return [m.to_dict(source) for m in CATALOG
            if category is None or m.category == category]


def get_by_id(model_id: str):
    for m in CATALOG:
        if m.id == model_id:
            return m
    return None


def filter_by_vram(vram_gb: float, source: str = DEFAULT_SOURCE,
                   category: Optional[str] = None) -> List[dict]:
    """Filtra per VRAM disponibile: restituisce i modelli che la VRAM e' sufficiente a eseguire; category filtra facoltativamente testo/video"""
    return [m.to_dict(source) for m in CATALOG
            if m.min_vram_gb <= vram_gb
            and (category is None or m.category == category)]
