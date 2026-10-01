"""Orchestrazione dei prompt video (percorso A: orchestrazione dei parametri con LLM + formato strutturato ufficiale)

Affida a un modello di grandi dimensioni l'espansione di una breve descrizione grezza dell'immagine data dall'utente, poi la assembla nel prompt finale secondo il formato
ufficiale dei prompt MiniMax H3 (vedi h3_prompt_format.py). L'obiettivo e' che «l'utente, senza orchestrare da se',
ottenga comunque video di alta qualita' che rientrano nella distribuzione di addestramento di H3».

Perche' non far produrre all'LLM direttamente il prompt finale: misurato, la prosa inglese libera ha qualita' scadente, la causa e' che non rispetta
il formato strutturato a campi di H3 (integrated_multimodal_description / retention_analysis
ecc.). La correttezza del formato e' garantita dall'assemblaggio nel codice, l'LLM compila solo il contenuto semantico, evitando che
ogni volta ricordi male il template.

Punti di progetto:
  - Riusa il canale di configurazione LLM di ai_tuner (get_config legge ai_config.json), senza introdurre
    nuove voci di configurazione API.
  - Implementa a parte una chiamata a temperature alta: ai_tuner._call_llm ha temp=0.3 fissa (la rifinitura dei parametri cerca stabilita'),
    l'espansione creativa richiede diversita', quindi si riusano solo la lettura della configurazione e l'interpretazione JSON tollerante.
  - Ogni errore restituisce None, e il chiamante (/generate) degrada con grazia al prompt originale.
  - Nessun ambiente personale cablato nel codice: nome del modello / indirizzo dell'interfaccia provengono tutti dalla ai_config salvata dall'utente.
"""

import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any

from .ai_tuner import get_config, _parse_llm_response
from .h3_prompt_format import compose_prompt


# Intervallo ragionevole dei parametri del workflow H3 turbo (il risultato dell'orchestrazione viene limitato a questo intervallo, per evitare valori arbitrari dell'LLM)
_STEPS_MIN, _STEPS_MAX = 4, 20
_CFG_MIN, _CFG_MAX = 0.5, 8.0


# Regole di scrittura comuni: l'LLM compila solo il contenuto semantico, non scrive i nomi dei campi finali (i campi sono assemblati dal codice)
_COMMON_RULES = """Regole di scrittura (determinano la qualita' del video, da rispettare assolutamente):
1. shot1 in inglese (H3 risponde meglio all'inglese), 40-80 parole, frasi naturali senza accumulare parole chiave separate da virgole.
2. shot1 inizia dichiarando stile e inquadratura, es. "Cinematic, live-action, a medium-wide shot frames ...";
   lo stile si deduce dall'intento dell'utente o dall'immagine di riferimento (realistico / 2D-animated / 3D CG / watercolor ecc.).
3. Il movimento di camera si scrive come frase d'azione inglese naturale, con le tre dimensioni «tipo di movimento + ampiezza + velocita'», ad esempio:
   "The camera pushes in with small amplitude at slow speed toward ..."。
   Elenco dei tipi di movimento: Zoom In/Out, Push In/Pull Out, Pan Left/Right, Truck Left/Right,
   Tilt Up/Down, Pedestal Up/Down, Arc Shot, Tracking Shot, Static Shot。
   Se ampiezza/velocita' non hanno significato si possono omettere (ampiezza media e velocita' normale di default non si scrivono).
4. Indicare chiaramente luce e atmosfera (soft morning light / golden hour / moody volumetric lighting).
5. soundscape: 1-4 frasi in inglese, che descrivono i suoni reali udibili nell'immagine (suoni ambientali + suoni di azioni fisiche +
   voci), come acqua, passi, vento, sfregamento di tessuto.
6. music: 1-2 frasi in inglese, che descrivono la colonna sonora di sottofondo riservata allo spettatore (strumenti + tempo + emozione); se l'utente non ha parlato di
   musica o vuole silenzio si scrive "N/A".
7. Conservare tutti gli elementi chiave dell'intento originale dell'utente, senza aggiungere dal nulla personaggi o trame che cambierebbero il soggetto."""

# Specifico R2V: l'ancoraggio dell'identita' e' la chiave della qualita'
_R2V_RULES = """

Questo e' un «video da immagine di riferimento», bisogna usare le sei sezioni ufficiali per bloccare l'identita' dei personaggi:
- subjects: per ogni soggetto dare un name stabile (es. "Subject 1"), refs (in quali immagini di riferimento compare,
  es. ["Picture 1","Picture 2"]), appearance (caratteristiche visibili concrete: forma del viso/acconciatura/accessori per capelli/colore e foggia
  dei vestiti ecc., piu' e' concreto meglio e', non scrivere etichette vuote come "poetico/cinematografico").
- summary: una frase che riassume chi fa cosa nell'intero video, citando i soggetti con <Subject N>.
- retention: per ogni soggetto elencare preserved: indicare chiaramente quali caratteristiche devono restare invariate (fully_preserved),
  e' il cuore del blocco dell'identita' di H3, bisogna ripetere qui le caratteristiche di aspetto chiave di subjects.
- Anche quando in shot1 si descrive l'azione bisogna citare i soggetti con <Subject N>, per mantenere l'identita' coerente."""


SYSTEM_PROMPT_T2V = """Sei un direttore della fotografia e storyboarder esperto, scrivi il contenuto visivo per il modello text-to-video MiniMax H3.

L'utente ti dara' una descrizione dell'immagine in una frase, in italiano, cinese o inglese. Produci un oggetto JSON:
{
  "shot1": "Inglese: dichiarazione di stile di questa inquadratura + soggetto + azione + scena + movimento di camera (frase naturale a tre dimensioni) + luce",
  "soundscape": "Inglese: suoni reali nell'immagine",
  "music": "Inglese: musica di sottofondo, N/A se assente",
  "steps": intero(4-20),
  "cfg": decimale(0.5-8.0),
  "reasoning": "Breve sintesi in italiano della logica di orchestrazione"
}

""" + _COMMON_RULES + """

Regole sui parametri: steps 8-12 per resa cinematografica realistica, default 8; cfg consigliato per H3 turbo intorno a 1.0, se la descrizione e' molto specifica
e si vuole un forte rispetto si usa 1.5-2.5, default 1.0.

Produci solo il JSON, senza alcun testo aggiuntivo ne' contenuto al di fuori di markdown."""


SYSTEM_PROMPT_R2V = """Sei un direttore della fotografia e storyboarder esperto, scrivi il contenuto per il reference-to-video (Ref2VA) di MiniMax H3.

L'utente ti dara' una descrizione dell'immagine (personaggi/soggetti dell'immagine sono gia' forniti dalle immagini di riferimento). Produci un oggetto JSON:
{
  "subjects": [{"name": "Subject 1", "refs": ["Picture 1","Picture 2"], "appearance": "caratteristiche concrete dell'aspetto in inglese"}],
  "summary": "Inglese: una frase che riassume chi fa cosa nell'intero video, citando con <Subject N>",
  "retention": [{"name": "Subject 1", "preserved": "Inglese: caratteristiche concrete che devono restare invariate"}],
  "shot1": "Inglese: dichiarazione di stile + azione descritta con <Subject N> + scena + movimento di camera (tre dimensioni) + luce",
  "soundscape": "Inglese: suoni reali nell'immagine",
  "music": "Inglese: musica di sottofondo, N/A se assente",
  "steps": intero(4-20),
  "cfg": decimale(0.5-8.0),
  "reasoning": "Breve sintesi in italiano della logica di orchestrazione"
}

""" + _COMMON_RULES + _R2V_RULES + """

Regole sui parametri: steps default 8; cfg default 1.0.

Produci solo il JSON, senza alcun testo aggiuntivo."""


def _call_llm_creative(cfg: Dict[str, Any], messages: list,
                       temperature: float = 0.85,
                       max_tokens: int = 1024) -> Optional[str]:
    """Chiamata compatibile OpenAI a temperature alta (per l'espansione creativa, diversa dalla chiamata a bassa temperatura del tuning)."""
    url = cfg.get("api_url", "").rstrip("/")
    if not url.endswith("/chat/completions"):
        if "/v1" in url:
            url = f"{url}/chat/completions"
        else:
            url = f"{url}/v1/chat/completions"

    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    payload = json.dumps({
        "model": cfg.get("model_name", ""),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
    except (urllib.error.URLError, urllib.error.HTTPError, Exception):
        return None
    return None


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def enhance_prompt(user_prompt: str, mode: str = "t2v",
                   picture_count: int = 1) -> Optional[Dict[str, Any]]:
    """Orchestra la descrizione grezza dell'utente in un prompt strutturato ufficiale H3 + parametri consigliati.

    mode: t2v / i2v / r2v. r2v usa le sei sezioni (con ancoraggio dell'identita'), gli altri usano i tre campi principali.
    picture_count: numero di immagini di riferimento / primo fotogramma (r2v lo usa per assegnare le etichette Picture ai subjects).

    In caso di successo restituisce {"prompt" (gia' assemblato nel formato ufficiale), "steps", "cfg", "reasoning"};
    LLM non configurato / chiamata fallita / interpretazione fallita restituiscono sempre None (il chiamante ripiega sul prompt originale).
    """
    if not user_prompt or not user_prompt.strip():
        return None

    cfg = get_config()
    if not cfg.get("api_url") or not cfg.get("model_name"):
        # L'utente non ha ancora configurato un LLM, impossibile orchestrare, si restituisce l'originale
        return None

    sys_prompt = SYSTEM_PROMPT_R2V if mode == "r2v" else SYSTEM_PROMPT_T2V
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt.strip()},
    ]
    raw = _call_llm_creative(cfg, messages,
                             max_tokens=1600 if mode == "r2v" else 1024)
    parsed = _parse_llm_response(raw)
    if not parsed or not parsed.get("shot1"):
        return None

    # Assembla il prompt finale secondo il template ufficiale (la correttezza del formato e' garantita dal codice)
    final = compose_prompt(parsed, mode=mode, picture_count=picture_count)
    if not final.strip():
        return None

    steps = int(_clamp(parsed.get("steps"), _STEPS_MIN, _STEPS_MAX, 8))
    cfd = _clamp(parsed.get("cfg"), _CFG_MIN, _CFG_MAX, 1.0)
    return {
        "prompt": final,
        "steps": steps,
        "cfg": round(cfd, 2),
        "reasoning": str(parsed.get("reasoning", "")).strip(),
    }
