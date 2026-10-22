"""Testo-immagine: percorso UNETLoader (Qwen-Image e simili, senza CLIP/VAE integrati) — v1.1.39, 2026-10-22

Integrato dal progetto originale (htobty/ReadyLLM, commit a0416b7), commenti tradotti in italiano.

Perche' un percorso a parte: i modelli immagine di ComfyUI stanno in due posti che non si parlano:
 * `checkpoints/`: pesi principali tipo SD/SDXL con UNET+CLIP+VAE nello stesso file, caricati in un colpo da CheckpointLoaderSimple;
 * `diffusion_models/`: solo un UNET (Qwen-Image 2.1 e' cosi'), quindi servono CLIPLoader + VAELoader, e la cartella e'
   condivisa con i pesi video come MiniMax H3.
Prima /image-models scansionava solo checkpoints: mettendo qwen_image in diffusion_models il menu mostrava un solo RealVisXL e
sembrava che «il modello immagine fosse sparito». Qui si aggiunge il secondo percorso.

L'abbinamento CLIP/VAE non si puo' interrogare (ComfyUI non sa quali tre file formano un set): si riconosce dai nomi dei file con le
regole di _PAIR_RULES, e solo le famiglie riconosciute vengono proposte; i pesi non riconosciuti (soprattutto i video) non si
mostrano, cosi' non si invia un workflow che fallirebbe per nodi mancanti.
"""

from __future__ import annotations

from typing import Optional

# (parola chiave del nome unet, priorita' delle parole chiave del CLIP, priorita' di quelle del VAE)
# Nota: l'ordine delle parole del CLIP conta. In locale ci sono sia qwen3vl_32b (encoder testuale di H3) sia qwen3vl_8b (quello di
# Qwen-Image), entrambi contengono "qwen": deve vincere la piu' lunga "qwen3vl_8b", altrimenti il CLIP del video finisce sul modello immagine.
_PAIR_RULES = [
    ("qwen_image", ("qwen3vl_8b", "qwen3vl", "qwen"), ("qwen_image", "qwen")),
    ("qwen-image", ("qwen3vl_8b", "qwen3vl", "qwen"), ("qwen_image", "qwen")),
]

# [2026-10-22 v1.1.39] Nel progetto originale _WEIGHT_EXTS era usata in resolve_name() ma non definita (NameError con nomi che hanno
# un'estensione diversa da quella nell'elenco): qui e' definita.
_WEIGHT_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf")

# Parametri di campionamento ufficiali di Qwen-Image: euler + simple, 20 passi, cfg 2.5, shift 3.1.
# Con cfg 6~7 (tipico di SDXL) l'immagine si sovraespone e compaiono bordi netti: per questo percorso i default sono separati.
QWEN_IMAGE_DEFAULTS = {
    "steps": 20,
    "cfg": 2.5,
    "sampler": "euler",
    "scheduler": "simple",
    "shift": 3.1,
}


def _options(engine, node: str, field: str) -> list:
    """Elenco dei valori ammessi per un ingresso, da /object_info/<nodo>.

    Passa dal _curl_json dell'engine (lo stesso canale di list_checkpoints: tunnel SSH o connessione diretta li sceglie l'engine),
    senza creare un altro client HTTP.
    """
    try:
        info = engine._curl_json(f"/object_info/{node}", timeout=15)
    except Exception:
        return []
    if not info:
        return []
    try:
        opts = info[node]["input"]["required"][field][0]
    except (KeyError, IndexError, TypeError):
        return []
    return [o for o in opts if isinstance(o, str)]


def list_unets(engine) -> list:
    """Tutti i pesi unet in diffusion_models (immagini e video mescolati)."""
    return _options(engine, "UNETLoader", "unet_name")


def list_clips(engine) -> list:
    return _options(engine, "CLIPLoader", "clip_name")


def list_vaes(engine) -> list:
    return _options(engine, "VAELoader", "vae_name")


def _pick(names: list, keywords: tuple) -> str:
    """Primo nome che contiene una parola chiave, in ordine di priorita'; stringa vuota se nessuno."""
    low = [(n, n.lower()) for n in names]
    for kw in keywords:
        for n, ln in low:
            if kw in ln:
                return n
    return ""


def pair_for(unet_name: str, clips: list, vaes: list) -> Optional[tuple]:
    """Abbina a un unet immagine la coppia (clip_name, vae_name); None per le famiglie non riconosciute."""
    if not unet_name:
        return None
    low = unet_name.lower()
    for unet_kw, clip_kws, vae_kws in _PAIR_RULES:
        if unet_kw not in low:
            continue
        clip = _pick(clips, clip_kws)
        # Anche i VAE video (minimax_h3_*) hanno «vae» nel nome: il VAE si cerca prima col nome della famiglia e solo dopo con la parola
        # generica, escludendo quelli palesemente video.
        vae = _pick(vaes, vae_kws)
        if not vae:
            vae = _pick([v for v in vaes if "minimax" not in v.lower()], ("vae",))
        if clip and vae:
            return clip, vae
        return None
    return None


def is_image_unet(unet_name: str) -> bool:
    low = (unet_name or "").lower()
    return any(kw in low for kw, _, _ in _PAIR_RULES)


def resolve_name(name: str, options: list) -> str:
    """Riporta il nome del modello arrivato dal frontend al nome file reale nell'elenco di ComfyUI.

    UNETLoader e gli altri nodi controllano che il valore sia nell'elenco e accettano solo il nome completo, per esempio
    ``qwen_image_2.1_int8_convrot.safetensors``; il menu del frontend (e il localStorage) puo' avere il nome senza estensione.
    Senza estensione l'intero workflow viene respinto con prompt_outputs_failed_validation e un laconico "Value not in list".

    Attenzione: non si puo' fare rsplit('.') sul nome ricevuto: in Qwen-Image c'e' un punto nella versione
    (``qwen_image_2.1_vae_bf16``) e tagliare all'ultimo punto toglierebbe anche «2.1». Si toglie solo l'estensione dei pesi dal lato
    elenco e si confronta il nome intero.
    """
    if not name or not options:
        return name
    if name in options:
        return name
    hits = [o for o in options if o.rsplit(".", 1)[0] == name]
    if hits:
        return hits[0]
    # il nome ricevuto ha un'estensione diversa da quella dell'elenco (.ckpt contro .safetensors)
    for ext in _WEIGHT_EXTS:
        if name.lower().endswith(ext):
            bare = name[: -len(ext)]
            hits = [o for o in options if o.rsplit(".", 1)[0] == bare]
            return hits[0] if hits else name
    return name


def build_unet_image_workflow(
    prompt: str,
    unet_name: str,
    clip_name: str,
    vae_name: str,
    negative_prompt: str = "",
    width: int = 1024,
    height: int = 1024,
    steps: int = QWEN_IMAGE_DEFAULTS["steps"],
    cfg: float = QWEN_IMAGE_DEFAULTS["cfg"],
    seed: Optional[int] = None,
    sampler: str = QWEN_IMAGE_DEFAULTS["sampler"],
    scheduler: str = QWEN_IMAGE_DEFAULTS["scheduler"],
    shift: float = QWEN_IMAGE_DEFAULTS["shift"],
    filename_prefix: str = "modeldeploy/image",
) -> dict:
    """Workflow testo-immagine per unet tipo Qwen-Image.

    I nodi sono numerati da 400, per non confondersi con il percorso video (1xx) e con quello checkpoint (3xx): negli errori di
    ComfyUI si capisce subito quale strada e' stata presa. EmptySD3LatentImage e non EmptyLatentImage: il primo ha 16 canali,
    la forma di latent dei DiT come Qwen-Image / SD3.
    """
    if seed is None:
        seed = 0
    return {
        "400": {"class_type": "UNETLoader",
                "inputs": {"unet_name": unet_name, "weight_dtype": "default"}},
        "401": {"class_type": "CLIPLoader",
                "inputs": {"clip_name": clip_name, "type": "qwen_image"}},
        "402": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        "403": {"class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["401", 0]}},
        "404": {"class_type": "CLIPTextEncode",
                "inputs": {"text": negative_prompt or "", "clip": ["401", 0]}},
        "405": {"class_type": "EmptySD3LatentImage",
                "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
        "406": {"class_type": "ModelSamplingAuraFlow",
                "inputs": {"model": ["400", 0], "shift": float(shift)}},
        "407": {"class_type": "KSampler",
                "inputs": {"model": ["406", 0], "positive": ["403", 0],
                           "negative": ["404", 0], "latent_image": ["405", 0],
                           "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                           "sampler_name": sampler, "scheduler": scheduler,
                           "denoise": 1.0}},
        "408": {"class_type": "VAEDecode",
                "inputs": {"samples": ["407", 0], "vae": ["402", 0]}},
        "409": {"class_type": "SaveImage",
                "inputs": {"images": ["408", 0], "filename_prefix": filename_prefix}},
    }
