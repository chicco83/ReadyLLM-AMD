#!/usr/bin/env python3
"""Avvio di backend (FastAPI) e frontend (Vite) di ReadyLLM-AMD

Versione: 1.1.4 — 2026-10-01 22:25

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


# [2026-10-01 v1.1.4] Cartelle su Google Drive per desktop ("Il mio Drive" / "My Drive"): e' un disco virtuale
# che fa fallire npm con centinaia di errori TAR_ENTRY_ERROR (EBADF / EPERM / UNKNOWN) mentre scrive
# migliaia di piccoli file in node_modules, lasciando l'installazione corrotta.
# Rimedio automatico (solo Windows): node_modules viene creato su disco LOCALE e collegato con una
# giunzione (mklink /J), cosi' npm scrive sul disco locale e Drive non vede i file.
SEGNI_DRIVE = ("il mio drive", "my drive", "google drive", "googledrive")


def su_drive(percorso: str) -> bool:
    p = percorso.lower().replace("\\", "/")
    return any(sg in p for sg in SEGNI_DRIVE)


def prepara_node_modules_locale():
    """Se il frontend e' su Drive (Windows), sposta node_modules su disco locale tramite giunzione."""
    if os.name != "nt" or not su_drive(FRONTEND):
        return
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    locale = os.path.join(base, "ReadyLLM-AMD", "node_modules")
    link = os.path.join(FRONTEND, "node_modules")
    print(f"⚠ Progetto su Google Drive: node_modules verra' creato in {locale} (giunzione)")
    os.makedirs(locale, exist_ok=True)
    if os.path.lexists(link):
        # Elimina la cartella corrotta (o la vecchia giunzione) senza seguire i file: rmdir /s /q
        subprocess.call(f'rmdir /s /q "{link}"', shell=True)
    if not os.path.lexists(link):
        subprocess.check_call(f'mklink /J "{link}" "{locale}"', shell=True)
    else:
        print("✗ Impossibile rimuovere frontend\\node_modules: sospendere Drive o spostare il progetto in C:\\dev")


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
            prepara_node_modules_locale()
            subprocess.check_call(CMD_INSTALLA_FRONTEND, cwd=FRONTEND, shell=True)

    if su_drive(FRONTEND) and "--installa" not in args:
        print("⚠ Progetto su Google Drive: se npm install ha dato errori TAR_ENTRY_ERROR rilanciare con --installa "
              "(usa node_modules locale) oppure spostare il progetto in C:\\dev")

    processi = []
    try:
        if avvia_be:
            print("▶ Backend:  http://127.0.0.1:8000")
            processi.append(subprocess.Popen(CMD_BACKEND, cwd=BACKEND))
        if avvia_fe:
            if not os.path.isdir(os.path.join(FRONTEND, "node_modules")):
                print("⚠ node_modules assente nel frontend: eseguire prima  python avvia.py --installa")
            print("▶ Frontend: http://localhost:3000")
            processi.append(subprocess.Popen(CMD_FRONTEND, cwd=FRONTEND, shell=True))
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
