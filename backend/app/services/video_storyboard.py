"""Generatore di storyboard (livello 1 dei video lunghi) + formato strutturato ufficiale H3

Affida a un modello di grandi dimensioni un tema/storia da scomporre in «storyboard a piu' inquadrature». Ogni inquadratura (shot) corrisponde a un breve video che
H3 puo' generare in una volta, e alla fine video_pipeline genera in serie in R2V e concatena in un video lungo di minuti.

Punto chiave: il campo prompt di ogni shot e' direttamente le sei sezioni ufficiali Ref2VA di H3 (subject_definitions/
summary/retention_analysis/detailed_description/overall_soundscape/non_diegetic_music），
assemblate da h3_prompt_format.compose_ref_prompt. Misurato: il formato ufficiale migliora nettamente la qualita' rispetto alla prosa libera,
e video_pipeline._generate_one_shot passa direttamente shot["prompt"], senza modifiche alla pipeline.

Punti di progetto:
  - Le subjects/retention globali si definiscono una volta e tutti i segmenti le riusano (stesso gruppo di immagini di riferimento per bloccare l'identita'), ogni segmento cambia solo
    detailed_description(shot1)/soundscape/music.
  - Riusa video_prompt._call_llm_creative (canale creativo a temperature alta) e la configurazione di ai_tuner.
  - Ogni segmento fornisce anche first_frame_prompt (per il text-to-image che produce primo fotogramma / immagine di riferimento).
  - In caso di errore restituisce None, il chiamante decide come degradare.
  - Nessun ambiente personale cablato nel codice.
"""

import json
from typing import Optional, Dict, Any, List

from .ai_tuner import _parse_llm_response
from .video_prompt import _call_llm_creative, get_config
from .h3_prompt_format import compose_ref_prompt


# Intervallo ragionevole di fotogrammi per segmento (@16fps: 80 fotogrammi~5s, 161 fotogrammi~10s), bilancia coerenza e VRAM
_LEN_MIN, _LEN_MAX = 49, 161


def _align_len(n: int) -> int:
    n = max(_LEN_MIN, min(_LEN_MAX, int(n)))
    k = round((n - 1) / 17.0)
    aligned = 17 * k + 1
    if aligned < _LEN_MIN:
        aligned = _LEN_MIN
    if aligned > _LEN_MAX:
        aligned = _LEN_MAX
    return aligned


SYSTEM_PROMPT = """Sei un regista e storyboarder. L'utente ti da' un tema per un video (eventualmente con una sinossi) e la durata totale desiderata, tu devi scomporlo in una serie di inquadrature consecutive, che alla fine MiniMax H3 reference-to-video (Ref2VA) generera' segmento per segmento e poi concatenera'.

Produci un oggetto JSON:
{
  "title": "titolo del video",
  "subjects": [{"name": "Subject 1", "refs": ["Picture 1","Picture 2"], "appearance": "Inglese: caratteristiche visibili concrete del soggetto (forma del viso/acconciatura/accessori per capelli/colore e foggia dei vestiti ecc., piu' e' concreto meglio e')"}],
  "retention": [{"name": "Subject 1", "preserved": "Inglese: caratteristiche chiave dell'aspetto che devono restare invariate"}],
  "shots": [
    {
      "index": 1,
      "title": "nome breve in italiano di questa inquadratura",
      "shot1": "Inglese: testo di [Shot 1] di questa inquadratura - dichiarazione di stile + azione descritta con <Subject N> + scena + movimento di camera + luce (40-70 parole)",
      "soundscape": "Inglese: suoni reali nell'immagine di questa inquadratura",
      "music": "Inglese: musica di sottofondo, N/A se assente",
      "first_frame_prompt": "Inglese: prompt dell'immagine statica del primo fotogramma di questa inquadratura, per il text-to-image (composizione/soggetto/luce, 30-50 parole)",
      "length": intero(49-161, a 16fps il numero di fotogrammi ~ secondi x 16)
    }
  ]
}

Regole di scomposizione (determinano la qualita' del video, da rispettare assolutamente):
1. Numero di inquadrature = durata totale desiderata / circa 6-8 secondi per segmento, arrotondato per eccesso, di solito 4-12.
2. La length di ogni segmento e' nell'intervallo 49-161 (circa 3-10 secondi), la somma dei segmenti ~ durata totale desiderata x 16.
3. subjects e retention sono globali: si definiscono una volta e tutte le inquadrature riusano lo stesso gruppo (e' il cuore del blocco dell'identita' dei personaggi di H3, retention.preserved deve ripetere le caratteristiche di aspetto chiave di subjects).
4. Ogni shot1 cita i soggetti con <Subject N> per descrivere l'azione; tra le inquadrature deve esserci avanzamento narrativo o cambiamento visivo, ma soggetto/scena/tono cromatico restano coerenti.
5. Il movimento di camera si scrive come frase d'azione inglese naturale, con le tre dimensioni tipo di movimento+ampiezza+velocita', es. "The camera pushes in with small amplitude at slow speed toward ...". Elenco dei tipi di movimento: Zoom In/Out, Push In/Pull Out, Pan Left/Right, Truck Left/Right, Tilt Up/Down, Arc Shot, Tracking Shot, Static Shot.
6. shot1 inizia dichiarando stile e inquadratura, es. "Cinematic, live-action, a medium shot frames ...".
7. first_frame_prompt deve poter generare in modo indipendente un fermo immagine di alta qualita', ed essere coerente con la descrizione dell'aspetto di subjects.

Produci solo il JSON, senza alcun testo aggiuntivo."""


def generate_storyboard(theme: str, total_seconds: int = 60,
                        max_shots: int = 12) -> Optional[Dict[str, Any]]:
    """Scompone il tema in uno storyboard, il prompt di ogni segmento e' nelle sei sezioni ufficiali H3.

    In caso di successo restituisce {"title", "shots": [{"index","title","prompt"(sei sezioni ufficiali),
    "first_frame_prompt","length"}, ...]}; LLM non configurato/errore restituisce None.
    """
    if not theme or not theme.strip():
        return None
    cfg = get_config()
    if not cfg.get("api_url") or not cfg.get("model_name"):
        return None

    user_msg = (
        f"Tema: {theme.strip()}\n"
        f"Durata totale desiderata: circa {int(total_seconds)} secondi\n"
        f"Al massimo {max_shots} inquadrature. Produci il JSON dello storyboard."
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    raw = _call_llm_creative(cfg, messages, temperature=0.8, max_tokens=4000)
    parsed = _parse_llm_response(raw)
    if not parsed or not isinstance(parsed.get("shots"), list) or not parsed["shots"]:
        return None

    # Definizione globale dell'identita' (riusata da tutti i segmenti)
    subjects = parsed.get("subjects") or []
    retention = parsed.get("retention") or []

    shots: List[Dict[str, Any]] = []
    for i, s in enumerate(parsed["shots"][:max_shots]):
        if not s.get("shot1"):
            continue
        # Con l'assemblatore si compone il contenuto semantico di questo segmento nelle sei sezioni ufficiali (la correttezza del formato e' garantita dal codice)
        data = {
            "subjects": subjects,
            "summary": s.get("summary") or (
                "The target video shows " + (subjects[0]["name"] if subjects else "the subject")
                + " in this shot."),
            "retention": retention,
            "shot1": s.get("shot1", ""),
            "soundscape": s.get("soundscape", ""),
            "music": s.get("music", ""),
        }
        prompt = compose_ref_prompt(data)
        if not prompt.strip():
            continue
        shots.append({
            "index": i + 1,
            "title": str(s.get("title", f"Inquadratura {i+1}")).strip(),
            "prompt": prompt,
            "first_frame_prompt": str(
                s.get("first_frame_prompt", s.get("shot1", ""))).strip(),
            "length": _align_len(s.get("length", 97)),
        })
    if not shots:
        return None
    return {"title": str(parsed.get("title", theme)).strip(), "shots": shots}
