#!/usr/bin/env python3
"""Avvio di backend (FastAPI) e frontend (Vite) di ReadyLLM-AMD

Versione: 1.1.3 — 2026-10-01 22:10

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
            subprocess.check_call(CMD_INSTALLA_FRONTEND, cwd=FRONTEND, shell=True)

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
