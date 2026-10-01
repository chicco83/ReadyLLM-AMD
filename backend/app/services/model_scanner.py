"""Scansione ricorsiva della cartella dei modelli (.gguf)

Versione: 1.1.0 (2026-10-01 21:40)

Cerca i file .gguf nella cartella `models_dir` del target E in tutte le sue
sottocartelle (prima veniva letto solo il livello principale).

I percorsi restituiti sono RELATIVI a models_dir (es. "qwen/Qwen3-8B-Q4_K_M.gguf"),
con il separatore nativo del sistema target, cosi' possono essere concatenati con
path_join(target, target.models_dir, model) come tutti gli altri moduli
(tuner, ai_tuner, deploy) senza altre modifiche.

Filtri applicati:
  - shard multi-file "xxx-00002-of-00005.gguf": si tiene solo il primo shard
    (llama.cpp carica gli altri automaticamente partendo dal -00001-of-N)
  - file "mmproj*.gguf": sono proiettori multimodali, non modelli avviabili
"""

import re

from .executor import Executor
from ..models.target import Target

# Shard successivi al primo: "-00002-of-00005"
_SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)

# Profondita' massima della ricerca (evita scansioni infinite con symlink ciclici)
MAX_DEPTH = 8


def _is_loadable(rel_path: str) -> bool:
    """True se il file e' un modello avviabile (non shard successivo, non mmproj)."""
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().startswith("mmproj"):
        return False
    m = _SHARD_RE.search(name)
    if m and int(m.group(1)) != 1:
        return False
    return True


def _relative(full: str, base: str, sep: str) -> str:
    """Converte un percorso assoluto in relativo a base, con separatore `sep`."""
    f = full.strip().replace("\\", "/")
    b = base.strip().replace("\\", "/").rstrip("/")
    # Confronto case-insensitive su Windows (le lettere di unita' possono differire)
    if f.lower().startswith(b.lower() + "/"):
        f = f[len(b) + 1:]
    return f.replace("/", sep)


def scan_models(executor: Executor, target: Target) -> list[str]:
    """Restituisce i modelli .gguf (percorsi relativi) in models_dir e sottocartelle."""
    base = target.models_dir
    if not base:
        return []

    if target.os == "windows":
        # dir /s /b: elenco ricorsivo con percorsi completi, un file per riga
        result = executor.run(f'dir /s /b "{base}\\*.gguf" 2>nul', timeout=30)
        sep = "\\"
    else:
        # -L segue i symlink; -maxdepth evita cicli; -iname per .GGUF maiuscolo
        result = executor.run(
            f'find -L "{base}" -maxdepth {MAX_DEPTH} -type f -iname "*.gguf" 2>/dev/null',
            timeout=30)
        sep = "/"

    models: list[str] = []
    if result.stdout:
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or not line.lower().endswith(".gguf"):
                continue
            rel = _relative(line, base, sep)
            if _is_loadable(rel):
                models.append(rel)
    return sorted(set(models), key=str.lower)
