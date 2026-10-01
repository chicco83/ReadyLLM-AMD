"""Statistiche d'uso dei token: accumula per giorno i token prompt/completion, persistite, non si perdono al riavvio del backend.

I dati sorgente prompt_tokens / completion_tokens sono i "valori cumulati dall'avvio" del motore di inferenza,
e si azzerano al riavvio del modello. Qui si fa un'accumulazione incrementale:
  - Normale: delta = current - last
  - Azzeramento (riavvio del modello): current < last, delta = current (la parte ricominciata da 0)
Suddivisi per giorno e salvati in ~/.model-deploy-assistant/token_stats.json, non si perdono al riavvio del backend.
"""

import json
import os
import threading
from datetime import date, timedelta

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".model-deploy-assistant")
STATS_FILE = os.path.join(CONFIG_DIR, "token_stats.json")
RETENTION_DAYS = 30

_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(STATS_FILE):
        return {}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATS_FILE)


def _prune_days(days: dict) -> None:
    """Conserva solo gli ultimi RETENTION_DAYS giorni, per evitare che il file cresca all'infinito."""
    cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
    for k in [k for k in days if k < cutoff]:
        del days[k]


def record_tokens(target_id: str, metrics: dict) -> None:
    """Chiamata dopo ogni raccolta, accumula l'incremento nel bucket del giorno corrente.

    Se non ci sono campi token (es. motore comfyui) salta. Chiamate ripetute a valori invariati danno delta=0,
    senza doppia accumulazione, quindi sia il canale snapshot sia quello ws possono chiamarla in sicurezza.
    """
    if not metrics or ("prompt_tokens" not in metrics and "completion_tokens" not in metrics):
        return
    cur_p = int(metrics.get("prompt_tokens", 0) or 0)
    cur_c = int(metrics.get("completion_tokens", 0) or 0)

    with _lock:
        data = _load()
        st = data.setdefault(target_id, {"last_prompt": 0, "last_completion": 0, "days": {}})
        last_p = st.get("last_prompt", 0)
        last_c = st.get("last_completion", 0)
        # Rilevamento dell'azzeramento: current < last indica un riavvio del modello, l'incremento e' current (la parte ricominciata da 0)
        delta_p = cur_p - last_p if cur_p >= last_p else cur_p
        delta_c = cur_c - last_c if cur_c >= last_c else cur_c
        if delta_p < 0:
            delta_p = 0
        if delta_c < 0:
            delta_c = 0

        today = date.today().isoformat()
        days = st.setdefault("days", {})
        day = days.setdefault(today, {"prompt": 0, "completion": 0})
        day["prompt"] += delta_p
        day["completion"] += delta_c

        st["last_prompt"] = cur_p
        st["last_completion"] = cur_c
        _prune_days(days)
        _save(data)


def get_total_stats(target_id: str) -> dict:
    """Consumo cumulato: somma prompt/completion di tutti i giorni registrati (non limitato dalla finestra di interrogazione)."""
    with _lock:
        data = _load()
    day_map = data.get(target_id, {}).get("days", {})
    total_p = sum(v.get("prompt", 0) for v in day_map.values())
    total_c = sum(v.get("completion", 0) for v in day_map.values())
    return {"prompt": total_p, "completion": total_c, "total": total_p + total_c}


def get_daily_stats(target_id: str, days: int = 14) -> list:
    """Restituisce [{date, prompt, completion}], con 0 per i giorni mancanti.

    Inizio della finestra: il primo giorno con dati (incluso oggi); se non ci sono ancora dati, ripiega sugli ultimi days giorni.
    """
    with _lock:
        data = _load()
    day_map = data.get(target_id, {}).get("days", {})
    today = date.today()
    if day_map:
        start = min(date.fromisoformat(d) for d in day_map)
    else:
        start = today - timedelta(days=days - 1)
    result = []
    cur = start
    while cur <= today:
        d = cur.isoformat()
        v = day_map.get(d, {"prompt": 0, "completion": 0})
        result.append({
            "date": d,
            "prompt": v.get("prompt", 0),
            "completion": v.get("completion", 0),
        })
        cur += timedelta(days=1)
    return result
