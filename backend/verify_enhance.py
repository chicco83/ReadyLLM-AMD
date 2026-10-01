"""Verifica end-to-end del nucleo: orchestrazione automatica dell'LLM -> output dell'assemblatore, controlla se e' una struttura ufficiale in sei sezioni valida.
Non dipende da ComfyUI, chiama direttamente enhance_prompt(mode=r2v) e controlla il final_prompt restituito."""
import sys
sys.path.insert(0, ".")
from app.services.video_prompt import enhance_prompt

PROMPT = ("Una ragazza in abito hanfu di stile antico accovacciata a lavare i panni lungo un ruscello in stile Jiangnan, nebbiolina del primo mattino, "
          "la camera si avvicina lentamente, atmosfera quieta e poetica")

r = enhance_prompt(PROMPT, mode="r2v", picture_count=2)
if not r:
    print("FAIL: enhance_prompt ha restituito None (LLM non configurato / chiamata fallita / interpretazione fallita)")
    sys.exit(1)

fp = r["prompt"]
print("===== final_prompt assemblato automaticamente =====")
print(fp)
print("\n===== steps=%s cfg=%s =====" % (r["steps"], r["cfg"]))
print("reasoning:", r["reasoning"])

# Controllo del formato: campi obbligatori delle sei sezioni
checks = {
    "subject_definitions:": "subject_definitions:" in fp,
    "summary:": "summary:" in fp,
    "retention_analysis:": "retention_analysis:" in fp,
    "detailed_description:": "detailed_description:" in fp,
    "overall_soundscape:": "overall_soundscape:" in fp,
    "non_diegetic_music:": "non_diegetic_music:" in fp,
    "[Shot 1]": "[Shot 1]" in fp,
    "fully_preserved": "fully_preserved" in fp,
    "<Subject": "<Subject" in fp,
    "<Picture": "<Picture" in fp,
    "Movimento di camera a tre dimensioni (amplitude/speed)": ("amplitude" in fp or "speed" in fp
                              or "Static Shot" in fp),
}
print("\n===== Controllo del formato =====")
allok = True
for k, v in checks.items():
    print("  [%s] %s" % ("OK" if v else "mancante", k))
    allok = allok and v
print("\nConclusione:", "✅ Sei sezioni ufficiali valide" if allok else "⚠️ Mancano dei campi, vedi sopra")
