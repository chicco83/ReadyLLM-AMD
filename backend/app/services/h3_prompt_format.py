"""Assemblatore del formato ufficiale dei prompt H3 (unica fonte di verita').

Secondo la skill ufficiale MiniMax-H3: .agents/skills/h3-prompt-writing/
  - references/base-en.txt (T2VA / I2VA: tre campi principali)
  - references/ref-en.txt (Ref2VA: sei sezioni)

Principio di progetto: l'LLM compila solo il «contenuto semantico» (aspetto del soggetto, azione, movimento di camera, suono ecc.),
questo modulo assembla quei contenuti nella stringa finale del
prompt con i nomi di campo, l'ordine e le formule fissi ufficiali. La correttezza del formato e' garantita dal codice, senza dipendere dal fatto che l'LLM ricordi
ogni volta il template giusto.

Tre modalita':
  t2v  puro text-to-video: integrated_multimodal_description + overall_soundscape + non_diegetic_music
  i2v  image-to-video: i tre campi precedenti + istruzione di allineamento I2VA sulla prima riga (formula fissa)
  r2v  reference-to-video: sei sezioni subject_definitions/summary/retention_analysis/
       detailed_description/overall_soundscape/non_diegetic_music

Ogni nostro segmento video e' un breve video generato in modo indipendente, in detailed_description si mette solo un singolo [Shot 1]
(la prima inquadratura senza timestamp, conforme alla regola ufficiale «il first shot non porta timestamp»). I timestamp multi-shot
sono riservati a futuri scenari con piu' shot nello stesso segmento.
"""

from typing import Dict, Any, List


def _shot_body(shot1: str) -> str:
    """Riordina il contenuto dello shot in un testo che inizia con la dichiarazione di stile [Shot 1]."""
    s = (shot1 or "").strip()
    if s.lower().startswith("[shot 1]"):
        return s
    return "[Shot 1] " + s


def _ref_phrase(refs: List[str]) -> str:
    """['Picture 1','Picture 2'] -> '<Picture 1> and <Picture 2>'。"""
    tags = ["<%s>" % r.strip() for r in refs if r and r.strip()]
    if not tags:
        return ""
    if len(tags) == 1:
        return tags[0]
    return ", ".join(tags[:-1]) + " and " + tags[-1]


def compose_ref_prompt(data: Dict[str, Any]) -> str:
    """R2V (Ref2VA) in sei sezioni. data deve contenere subjects/summary/retention/shot1/
    soundscape/music. Se manca un campo si salta quella sezione, per garantire che non si blocchi."""
    subjects = data.get("subjects") or []
    retention = data.get("retention") or []
    parts: List[str] = []

    # 1. subject_definitions
    if subjects:
        lines = []
        for sub in subjects:
            name = str(sub.get("name", "")).strip()
            if not name:
                continue
            rp = _ref_phrase(sub.get("refs") or [])
            app = str(sub.get("appearance", "")).strip()
            lead = "<%s>" % name
            if rp:
                lines.append("%s is %s, %s." % (lead, rp, app) if app
                             else "%s is %s." % (lead, rp))
            else:
                lines.append("%s is %s." % (lead, app))
        if lines:
            parts.append("subject_definitions:\n" + "\n".join(lines))

    # 2. summary
    summary = str(data.get("summary", "")).strip()
    if summary:
        if not summary.startswith("["):
            summary = "[reference generation] " + summary
        parts.append("summary:\n" + summary)

    # 3. retention_analysis
    if retention:
        rlines = []
        for r in retention:
            name = str(r.get("name", "")).strip()
            if not name:
                continue
            preserved = str(r.get("preserved", "")).strip()
            # A volte l'LLM scrive «fully_preserved» nel campo preserved stesso, duplicando
            # il prefisso «fully_preserved - » del template sotto: lo si toglie, cosi' compare una sola volta.
            if preserved.lower().startswith("fully_preserved"):
                preserved = preserved[len("fully_preserved"):].lstrip(" -–:：").strip()
            rlines.append("<%s> (appears in [Shot 1]): fully_preserved - %s"
                          % (name, preserved))
        if rlines:
            parts.append("retention_analysis:\n" + "\n".join(rlines))

    # 4. detailed_description
    shot1 = str(data.get("shot1", "")).strip()
    if shot1:
        parts.append("detailed_description:\n" + _shot_body(shot1))

    # 5. overall_soundscape
    scape = str(data.get("soundscape", "")).strip()
    if scape:
        parts.append("overall_soundscape:\n" + scape)

    # 6. non_diegetic_music
    music = str(data.get("music", "")).strip() or "N/A"
    parts.append("non_diegetic_music:\n" + music)

    return "\n\n".join(parts)


def compose_base_prompt(data: Dict[str, Any], mode: str = "t2v",
                        picture_count: int = 1) -> str:
    """Tre campi principali di T2VA / I2VA. Con mode=i2v sulla prima riga si aggiunge l'istruzione di allineamento fissa."""
    parts: List[str] = []

    # Riga dell'istruzione di allineamento I2VA (formula fissa ufficiale)
    if mode == "i2v":
        parts.append(
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced.")

    shot1 = str(data.get("shot1", "")).strip()
    if shot1:
        parts.append("integrated_multimodal_description: " + _shot_body(shot1))

    scape = str(data.get("soundscape", "")).strip()
    if scape:
        parts.append("overall_soundscape: " + scape)

    music = str(data.get("music", "")).strip() or "N/A"
    parts.append("non_diegetic_music: " + music)

    return "\n\n".join(parts)


def compose_prompt(data: Dict[str, Any], mode: str = "t2v",
                   picture_count: int = 1) -> str:
    """Smistamento per modalita'. mode in {t2v, i2v, r2v}."""
    if mode == "r2v":
        return compose_ref_prompt(data)
    return compose_base_prompt(data, mode=mode, picture_count=picture_count)


if __name__ == "__main__":
    # Autotest: l'output assemblato deve rispettare la struttura ufficiale in sei sezioni
    d = {
        "subjects": [{"name": "Subject 1",
                      "refs": ["Picture 1", "Picture 2"],
                      "appearance": "the young woman with an oval face, "
                                    "black hanging bun with a white jade hairpin, "
                                    "pale-cyan qixiong ruqun Hanfu"}],
        "summary": "The target video shows <Subject 1> washing cloth by a canal.",
        "retention": [{"name": "Subject 1",
                       "preserved": "the oval face, hanging bun with jade hairpin, "
                                    "and pale-cyan Hanfu are retained."}],
        "shot1": "Cinematic, live-action, a medium side-view shot frames "
                 "<Subject 1> crouching beside blue stone steps. The camera "
                 "pushes in with small amplitude at slow speed.",
        "soundscape": "Gentle lapping of canal water and soft fabric rustling.",
        "music": "A restrained solo guqin melody at a slow tempo.",
    }
    print("===== R2V in sei sezioni =====")
    print(compose_ref_prompt(d))
    print("\n===== I2V tre campi =====")
    print(compose_base_prompt({"shot1": d["shot1"],
                               "soundscape": d["soundscape"],
                               "music": d["music"]}, mode="i2v"))
