#!/usr/bin/env python3
"""Avvio di backend (FastAPI) e frontend (Vite) di ReadyLLM-AMD

Versione: 1.1.5 — 2026-10-01 22:40

Uso (dalla radice del progetto, Windows / Linux / macOS):
    python avvia.py              # avvia backend + frontend
    python avvia.py --installa   # prima installa le dipendenze (pip install -r + npm install), poi avvia
    python avvia.py --comandi    # stampa soltanto i comandi manuali, senza avviare nulla
    python avvia.py --backend    # solo backend
    python avvia.py --frontend   # solo frontend

Comandi manuali equivalenti (due terminali separati):
    1) cd backend  &&  pip install -r requirements.txt  &&  uvicorn app.main:app --host 127.0.0.1 --port 8000
    2) cd frontend &&  npm install  &&  npm run dev        (interfaccia su http://localhost:3000)

Ctrl+C ferma entrambi i processi.
"""

import os
import shutil
import subprocess
import sys
import time

RADICE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(RADICE, "backend")
FRONTEND = os.path.join(RADICE, "frontend")

# Comandi (come liste per subprocess; su Windows npm e' npm.cmd -> shell=True lo risolve)
CMD_INSTALLA_BACKEND = [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
CMD_INSTALLA_FRONTEND = "npm install"
CMD_BACKEND = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
CMD_FRONTEND = "npm run dev"


# [2026-10-01 v1.1.5] Cartelle su Google Drive per desktop ("Il mio Drive" / "My Drive"): e' un disco virtuale
# che fa fallire npm con centinaia di errori TAR_ENTRY_ERROR (EBADF / EPERM / UNKNOWN) mentre scrive
# migliaia di piccoli file in node_modules.
# [2026-10-01 v1.1.4, SOSTITUITO] Si era provato a collegare node_modules a una cartella locale con una
# giunzione (mklink /J), ma fallisce: "sono necessari volumi NTFS locali" (G: di Drive non e' NTFS).
# Soluzione attuale (solo Windows): il frontend viene COPIATO in una cartella locale
# (%LOCALAPPDATA%\ReadyLLM-AMD\frontend, con robocopy, escludendo node_modules) e npm/vite girano da li'.
# La copia si rinnova a ogni avvio, quindi le modifiche ai sorgenti su Drive vengono riprese al riavvio
# (non c'e' hot-reload tra Drive e la copia locale mentre il server e' acceso).
SEGNI_DRIVE = ("il mio drive", "my drive", "google drive", "googledrive")


def su_drive(percorso: str) -> bool:
    p = percorso.lower().replace("\\", "/")
    return any(sg in p for sg in SEGNI_DRIVE)


def cartella_frontend() -> str:
    """Cartella da cui eseguire npm/vite: quella del progetto, o la copia locale se il progetto e' su Drive."""
    if os.name == "nt" and su_drive(FRONTEND):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ReadyLLM-AMD", "frontend")
    return FRONTEND


def sincronizza_frontend_locale() -> str:
    """Se serve, copia il frontend su disco locale (robocopy /MIR, node_modules escluso). Restituisce la cartella da usare."""
    dest = cartella_frontend()
    if dest == FRONTEND:
        return FRONTEND
    print(f"⚠ Progetto su Google Drive: il frontend viene eseguito da una copia locale: {dest}")
    os.makedirs(dest, exist_ok=True)
    # /MIR specchia la cartella; /XD node_modules evita di cancellare/copiare le dipendenze; exit code < 8 = successo
    rc = subprocess.call(f'robocopy "{FRONTEND}" "{dest}" /MIR /XD node_modules dist /NFL /NDL /NJH /NJS /NP', shell=True)
    if rc >= 8:
        raise SystemExit(f"✗ Copia del frontend non riuscita (robocopy codice {rc})")
    return dest


def stampa_comandi():
    print("Terminale 1 (backend):")
    print(f"  cd {BACKEND}\n  pip install -r requirements.txt\n  uvicorn app.main:app --host 127.0.0.1 --port 8000\n")
    print("Terminale 2 (frontend):")
    print(f"  cd {FRONTEND}\n  npm install\n  npm run dev      # http://localhost:3000")


def main():
    args = set(sys.argv[1:])
    if "--comandi" in args:
        stampa_comandi()
        return
    solo_be, solo_fe = "--backend" in args, "--frontend" in args
    avvia_be = solo_be or not solo_fe
    avvia_fe = solo_fe or not solo_be

    if "--installa" in args:
        if avvia_be:
            subprocess.check_call(CMD_INSTALLA_BACKEND, cwd=BACKEND)
        if avvia_fe:
            subprocess.check_call(CMD_INSTALLA_FRONTEND, cwd=sincronizza_frontend_locale(), shell=True)

    processi = []
    try:
        if avvia_be:
            print("▶ Backend:  http://127.0.0.1:8000")
            processi.append(subprocess.Popen(CMD_BACKEND, cwd=BACKEND))
        if avvia_fe:
            fe_dir = sincronizza_frontend_locale()
            if not os.path.isdir(os.path.join(fe_dir, "node_modules")):
                print("⚠ node_modules assente nel frontend: eseguire prima  python avvia.py --installa")
            print("▶ Frontend: http://localhost:3000")
            processi.append(subprocess.Popen(CMD_FRONTEND, cwd=fe_dir, shell=True))
        # Resta in attesa; se un processo termina da solo si ferma anche l'altro
        while all(p.poll() is None for p in processi):
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nArresto in corso...")
    finally:
        for p in processi:
            if p.poll() is None:
                p.terminate()
        for p in processi:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
