"""Orchestrazione del prompt per testo-immagine — v1.1.39, 2026-10-22

Integrato dal progetto originale (htobty/ReadyLLM, commit a0416b7), tradotto in italiano.

Il percorso video (video_prompt.py) produce prompt a storyboard strutturati per MiniMax H3: darli a SDXL / Qwen-Image non funziona
(questi modelli vogliono una descrizione dell'immagine piu' etichette di qualita', non campi di camera/azione/audio). Per le immagini
c'e' quindi un system prompt a parte che fa una cosa sola: espandere la frase colloquiale dell'utente in una descrizione adatta al
modello, con un suggerimento prudente sui passi.

Se l'LLM non e' disponibile (non configurato / chiamata fallita / risposta non interpretabile) restituisce None e il chiamante ripiega
sul prompt originale: la generazione non viene mai bloccata.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

# Il testo del system prompt e' in italiano; l'LLM deve comunque rispondere con un prompt IN INGLESE (i modelli rispondono meglio
# alle etichette inglesi). Versione originale: stesso contenuto, in cinese.
SYSTEM_PROMPT_IMAGE = """Sei un ingegnere di prompt per la generazione di immagini, al servizio di modelli come Stable Diffusion /
SDXL / Qwen-Image. L'utente ti da' una frase molto colloquiale che descrive l'immagine: devi espanderla in un prompt con cui il modello
produce buone immagini.

Requisiti:
1. Scrivi il prompt in INGLESE (questi modelli sono piu' sensibili alle etichette inglesi): soggetto + scena + composizione +
   luce + materiali, al massimo 5 frasi, senza accumulare aggettivi inutili.
2. Non scrivere movimenti di camera, linee temporali o audio: sono cose da video.
3. Non scrivere risoluzione, campionatore, passi o altri parametri: li controlla il chiamante.
4. Mantieni l'intento dell'utente, senza inventare identita' di persone, marchi o testi.

Rispondi solo con JSON, senza spiegazioni ne' blocchi di codice Markdown:
{"prompt": "prompt in inglese", "reasoning": "una frase in italiano che dice quali informazioni chiave hai aggiunto"}"""


def _get_config() -> Dict[str, Any]:
    """Riusa la configurazione LLM gia' usata dal percorso video, per non avere due punti di configurazione."""
    try:
        from .video_prompt import get_config  # import ritardato per evitare cicli
        return get_config() or {}
    except Exception:
        return {}


def _endpoint(api_url: str) -> str:
    """Normalizza l'api_url della configurazione nell'indirizzo completo di chat/completions."""
    u = (api_url or "").rstrip("/")
    if not u:
        return ""
    if u.endswith("/chat/completions"):
        return u
    if not u.endswith("/v1"):
        u = u + "/v1"
    return u + "/chat/completions"


def _call_llm(cfg: Dict[str, Any], user_prompt: str) -> Optional[str]:
    url = _endpoint(cfg.get("api_url") or "")
    model = cfg.get("model_name") or ""
    if not url or not model:
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_IMAGE},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 600,
    }
    headers = {"Content-Type": "application/json"}
    key = cfg.get("api_key") or ""
    if key:
        headers["Authorization"] = "Bearer " + key

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _parse(raw: str) -> Optional[Dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        # toglie l'involucro ```json ... ```
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    prompt = (obj.get("prompt") or "").strip()
    if not prompt:
        return None
    return {
        "prompt": prompt,
        "reasoning": (obj.get("reasoning") or "").strip(),
    }


def enhance_prompt(user_prompt: str) -> Optional[Dict[str, Any]]:
    """Espande la descrizione colloquiale in un prompt testo-immagine; in caso di errore restituisce sempre None (il chiamante ripiega)."""
    if not user_prompt or not user_prompt.strip():
        return None
    cfg = _get_config()
    raw = _call_llm(cfg, user_prompt.strip())
    if not raw:
        return None
    return _parse(raw)
