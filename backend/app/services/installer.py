"""Rilevamento del motore di inferenza e installazione con un clic

Pensato per tutti gli utenti: se sulla macchina target non e' ancora installato llama.cpp (llama-server),
offre l'installazione con un clic. Il metodo di installazione dipende dal sistema operativo di destinazione:
  - Windows: scarica il pacchetto precompilato ufficiale (CUDA / HIP-ROCm / Vulkan / CPU) e lo decomprime
  - macOS: installazione con Homebrew
  - Linux: compilazione dai sorgenti (CUDA / ROCm / Vulkan / CPU)

L'installazione e' un'operazione lunga: si usa un thread in background + polling dei log, per evitare timeout HTTP.
"""

import threading
import time
import uuid
from typing import Optional, List

from .executor import Executor
from ..models.target import Target

# Tabella globale dei task di installazione: job_id -> {status, logs, target_id, result}
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


# ==================== Rilevamento ====================

def detect_engine(executor: Executor, target: Target) -> dict:
    """Rileva se sulla macchina target e' installato il motore di inferenza scelto (smistamento per engine_type)"""
    engine_type = getattr(target, "engine_type", "llama_cpp") or "llama_cpp"
    if engine_type == "vllm":
        return _detect_vllm(executor, target)
    if engine_type == "sglang":
        return _detect_sglang(executor, target)
    if engine_type == "comfyui":
        return _detect_comfyui(executor, target)
    return _detect_llama(executor, target)


def _detect_comfyui(executor: Executor, target: Target) -> dict:
    """Rileva se ComfyUI e' installato: verifica l'esistenza del punto di ingresso main.py nella cartella radice di installazione.
    Per ComfyUI engine_path indica la cartella radice di installazione (non un eseguibile)."""
    d = target.engine_path
    if not d:
        return {"installed": False, "engine": "comfyui", "path": "", "version": "",
                "reason": "Cartella di installazione di ComfyUI non configurata (engine_path deve puntare alla cartella radice di ComfyUI)"}
    main_py = _join(d, "main.py")
    if target.os == "windows":
        found = "FOUND" in executor.run(f'if exist "{main_py}" (echo FOUND)').stdout
    else:
        found = "FOUND" in executor.run(f'test -f "{main_py}" && echo FOUND').stdout
    version = ""
    if found:
        # Legge il numero di versione di ComfyUI (hash breve del commit)
        vr = executor.run(f'cd "{d}" && git rev-parse --short HEAD 2>&1', timeout=10)
        if vr.ok:
            version = vr.stdout.strip()
    return {
        "installed": found, "engine": "comfyui", "path": d, "version": version,
        "reason": "" if found else "ComfyUI (main.py) non trovato nella cartella indicata",
    }


def _join(base: str, name: str) -> str:
    """Concatenazione di percorsi multipiattaforma (barra rovesciata su Windows / barra diretta altrove)."""
    if "\\" in base or ":" in base and "/" not in base:
        sep = "\\"
    elif base.endswith("/") or base.endswith("\\"):
        sep = ""
    else:
        sep = "/"
    if sep == "":
        return base + name
    return base.rstrip("/\\") + sep + name


def _detect_llama(executor: Executor, target: Target) -> dict:
    """Rileva se il binario llama-server esiste"""
    exe = target.engine_path
    if not exe:
        return {"installed": False, "engine": "llama_cpp", "reason": "Percorso del motore non configurato", "path": ""}

    if target.os == "windows":
        result = executor.run(f'if exist "{exe}" (echo FOUND)')
        found = "FOUND" in result.stdout
    else:
        result = executor.run(f'test -f "{exe}" && echo FOUND')
        found = "FOUND" in result.stdout

    version = ""
    if found:
        vr = executor.run(f'"{exe}" --version 2>&1 | head -1', timeout=10)
        version = vr.stdout.strip()

    return {
        "installed": found,
        "engine": "llama_cpp",
        "path": exe,
        "version": version,
        "reason": "" if found else "llama-server non trovato nel percorso indicato",
    }


def _detect_vllm(executor: Executor, target: Target) -> dict:
    """Rileva se vLLM e' utilizzabile (dopo l'installazione con pip il comando vllm e' nel PATH)"""
    cmd = target.engine_path or "vllm"
    if target.os == "windows":
        # vLLM non supporta Windows in modo nativo, si suggerisce WSL2
        return {
            "installed": False, "engine": "vllm", "path": cmd, "version": "",
            "reason": "vLLM non supporta l'esecuzione nativa su Windows: distribuirlo in WSL2 (Linux) oppure usare llama.cpp",
            "windows_note": True,
        }
    result = executor.run(f"{cmd} --version 2>&1", timeout=25)
    out = (result.stdout or "").lower()
    not_found = "not found" in out or "no module" in out or "command not found" in out
    installed = result.ok and any(c.isdigit() for c in out) and not not_found
    version = ""
    if installed:
        for ln in result.stdout.splitlines():
            if any(c.isdigit() for c in ln):
                version = ln.strip()
                break
    return {
        "installed": installed, "engine": "vllm", "path": cmd, "version": version,
        "reason": "" if installed else "Comando vllm non rilevato, installarlo prima (pip install vllm)",
    }


def _detect_sglang(executor: Executor, target: Target) -> dict:
    """Rileva se SGLang e' utilizzabile (dopo l'installazione con pip/uv il comando sglang e' nel PATH)"""
    cmd = target.engine_path or "sglang"
    if target.os == "windows":
        # Le istruzioni ufficiali di installazione di SGLang riguardano Linux + GPU NVIDIA
        return {
            "installed": False, "engine": "sglang", "path": cmd, "version": "",
            "reason": "Le istruzioni ufficiali di installazione di SGLang riguardano Linux + GPU NVIDIA: distribuirlo in WSL2 (Linux) oppure usare llama.cpp",
            "windows_note": True,
        }
    result = executor.run(f"{cmd} --version 2>&1", timeout=25)
    out = (result.stdout or "").lower()
    not_found = "not found" in out or "no module" in out or "command not found" in out
    installed = result.ok and any(c.isdigit() for c in out) and not not_found
    version = ""
    if installed:
        for ln in result.stdout.splitlines():
            if any(c.isdigit() for c in ln):
                version = ln.strip()
                break
    return {
        "installed": installed, "engine": "sglang", "path": cmd, "version": version,
        "reason": "" if installed else "Comando sglang non rilevato, installarlo prima (pip install sglang, richiede ambiente CUDA)",
    }


# ==================== Gestione dei task di installazione ====================

def _append_log(job_id: str, line: str):
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job["logs"].append({"t": time.strftime("%H:%M:%S"), "msg": line})


def _run_step(executor: Executor, job_id: str, cmd: str, desc: str, timeout: int = 600):
    """Esegue un passo e ne registra il log, restituisce ExecResult"""
    _append_log(job_id, f"▶ {desc}")
    result = executor.run(cmd, timeout=timeout)
    for ln in (result.stdout or "").splitlines()[-5:]:
        if ln.strip():
            _append_log(job_id, f"  {ln.strip()}")
    if not result.ok:
        for ln in (result.stderr or "").splitlines()[-5:]:
            if ln.strip():
                _append_log(job_id, f"  [err] {ln.strip()}")
    return result


def get_job(job_id: str) -> Optional[dict]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs() -> List[dict]:
    with _LOCK:
        return [{"job_id": j["job_id"], "status": j["status"],
                 "target_id": j["target_id"]} for j in _JOBS.values()]


# ==================== Scelta backend llama.cpp (v1.1.0, 2026-10-01) ====================
# Valutazione ROCm vs Vulkan (dettagli in MANUAL / CONTEXT):
#   - Vulkan: nessun SDK da installare (basta il driver grafico: Adrenalin su Windows,
#     Mesa RADV su Linux); funziona su RDNA2/3/4 compresa RX 9070 XT (gfx1201). E' la
#     scelta "funziona subito" e per la generazione token (tg) e' spesso alla pari con ROCm.
#   - ROCm/HIP: di solito piu' veloce nel prompt processing (pp); su Windows il pacchetto
#     precompilato "hip-radeon" porta con se' le DLL HIP; su Linux richiede ROCm >= 6.4
#     installato (gfx1201 non e' supportato dalle versioni precedenti) e compilazione da sorgente.
#   - Quindi: "auto" -> NVIDIA=cuda, AMD=vulkan (default sicuro), Apple=metal, altro=cpu;
#     l'utente puo' forzare "rocm" da Impostazioni quando ROCm/HIP e' installato.

LLAMA_BACKENDS = ("auto", "cuda", "rocm", "vulkan", "cpu")


def resolve_llama_backend(executor: Executor, target: Target) -> str:
    """Restituisce il backend effettivo (cuda/rocm/vulkan/metal/cpu) per questo target."""
    choice = (getattr(target, "llama_backend", "auto") or "auto").lower()
    if target.os == "macos":
        return "metal"                      # brew installa gia' la build Metal
    if choice in LLAMA_BACKENDS and choice != "auto":
        return choice
    from .collectors import _detect_gpu_static
    try:
        gpu = _detect_gpu_static(executor, target) or {}
    except Exception:
        gpu = {}
    vendor = gpu.get("vendor", "")
    if vendor == "nvidia":
        return "cuda"
    if vendor in ("amd", "intel"):
        return "vulkan"
    return "cpu"


# Preferenze di asset nelle release GitHub di llama.cpp (Windows), in ordine di priorita'.
# Si confrontano con i nomi reali: se nessuno combacia si elencano gli asset disponibili.
_WIN_ASSET_PATTERNS = {
    "cuda":   ["bin-win-cuda-12", "bin-win-cuda-cu12", "bin-win-cuda"],
    "rocm":   ["bin-win-hip-radeon", "bin-win-hip"],
    "vulkan": ["bin-win-vulkan"],
    "cpu":    ["bin-win-cpu", "bin-win-avx2"],
}


# ==================== Script di installazione per piattaforma ====================

def _install_windows(executor: Executor, job_id: str, target: Target) -> str:
    """Scarica il pacchetto precompilato ufficiale (secondo il backend scelto) e lo decomprime, restituisce l'engine_path dopo l'installazione"""
    install_dir = r"C:\llama"
    _run_step(executor, job_id,
              f'powershell -Command "New-Item -ItemType Directory -Force -Path {install_dir} | Out-Null"',
              "Creazione della cartella di installazione C:\\llama")

    # [2026-10-01 v1.1.0] Il pacchetto dipende dal backend scelto (cuda/rocm/vulkan/cpu).
    # Versione precedente (sostituita): scaricava SEMPRE il pacchetto CUDA, inutile su Radeon:
    #   $a=$r.assets | Where-Object { $_.name -match 'bin-win-cuda-cu12' -and $_.name -match 'x64' } | Select-Object -First 1
    backend = resolve_llama_backend(executor, target)
    _append_log(job_id, f"▶ Backend llama.cpp selezionato: {backend.upper()} "
                        f"(impostazione: {getattr(target, 'llama_backend', 'auto')})")
    list_cmd = (
        'powershell -Command "'
        "$ProgressPreference='SilentlyContinue'; "
        "$r=Invoke-RestMethod -Uri 'https://api.github.com/repos/ggml-org/llama.cpp/releases/latest'; "
        "$r.assets | ForEach-Object { Write-Output $_.browser_download_url }\""
    )
    _append_log(job_id, "▶ Ricerca dell'ultima release precompilata")
    list_res = executor.run(list_cmd, timeout=60)
    urls = [ln.strip() for ln in list_res.stdout.splitlines() if ln.strip().startswith("http")]
    url = ""
    for pat in _WIN_ASSET_PATTERNS.get(backend, []):
        for u in urls:
            name = u.rsplit("/", 1)[-1].lower()
            if pat in name and "x64" in name and not name.startswith("cudart") and name.endswith(".zip"):
                url = u
                break
        if url:
            break
    if not url:
        avail = ", ".join(u.rsplit("/", 1)[-1] for u in urls if "win" in u.lower()) or "nessuno"
        raise RuntimeError(
            f"Nessun pacchetto Windows per il backend {backend} nell'ultima release "
            f"(rete/GitHub non raggiungibile?). Asset Windows disponibili: {avail}")
    _append_log(job_id, f"  Sorgente download: {url}")

    zip_path = r"C:\llama\llama.zip"
    _run_step(executor, job_id,
              f'powershell -Command "$ProgressPreference=\'SilentlyContinue\'; '
              f'Invoke-WebRequest -Uri \'{url}\' -OutFile \'{zip_path}\'"',
              "Download del pacchetto precompilato (puo' essere grande, attendere)", timeout=900)

    _run_step(executor, job_id,
              f'powershell -Command "Expand-Archive -Path \'{zip_path}\' -DestinationPath \'{install_dir}\' -Force"',
              "Decompressione del pacchetto di installazione")

    # Individua llama-server.exe (dopo la decompressione si trova in una sottocartella)
    find_cmd = (
        f'powershell -Command "Get-ChildItem -Path {install_dir} -Recurse -Filter llama-server.exe '
        '| Select-Object -First 1 -ExpandProperty FullName"'
    )
    fr = executor.run(find_cmd, timeout=30)
    exe_path = ""
    for ln in fr.stdout.splitlines():
        if ln.strip().lower().endswith("llama-server.exe"):
            exe_path = ln.strip()
            break
    if not exe_path:
        raise RuntimeError("llama-server.exe non trovato dopo la decompressione")
    _append_log(job_id, f"  Percorso del motore: {exe_path}")
    return exe_path


def _install_sglang(executor: Executor, job_id: str, target: Target) -> str:
    """Installa SGLang con pip (solo Linux/macOS, Windows non supporta l'esecuzione nativa)

    La documentazione ufficiale richiede Python 3.10+ e ambiente CUDA, l'installazione avviene con pip/uv
    (uv pip install --prerelease=allow sglang, equivalente con pip).
    """
    if target.os == "windows":
        raise RuntimeError(
            "Le istruzioni ufficiali di installazione di SGLang riguardano Linux + GPU NVIDIA: installarlo in WSL2 (Linux) oppure usare llama.cpp")

    _run_step(executor, job_id, "command -v pip3 || command -v pip",
              "Verifica della disponibilita' di pip", timeout=20)

    # Rileva CUDA / GPU NVIDIA (SGLang e' pensato soprattutto per NVIDIA)
    cuda = executor.run(
        "command -v nvcc && nvidia-smi --query-gpu=name --format=csv,noheader", timeout=15)
    if cuda.stdout.strip():
        _append_log(job_id, f"  GPU/CUDA rilevata: {cuda.stdout.strip().splitlines()[0]}")
    else:
        _append_log(job_id, "  CUDA non rilevato: SGLang richiede un ambiente con GPU NVIDIA, dopo l'installazione potrebbe non funzionare correttamente")

    _run_step(executor, job_id,
              "pip3 install -U sglang 2>&1 | tail -20 || pip install -U sglang 2>&1 | tail -20",
              "Installazione di SGLang con pip (pacchetto grande e lungo, attendere)", timeout=3600)

    fr = executor.run("command -v sglang")
    exe_path = fr.stdout.strip().splitlines()[-1] if fr.stdout.strip() else ""
    if not exe_path:
        raise RuntimeError("Installazione completata ma comando sglang non trovato, controllare l'output di pip o il PATH")
    _append_log(job_id, f"  Percorso del motore: {exe_path}")
    return exe_path


def _install_macos(executor: Executor, job_id: str, target: Target) -> str:
    """Installa llama.cpp con Homebrew"""
    # Verifica brew
    brew_check = executor.run("command -v brew")
    if not brew_check.stdout:
        raise RuntimeError("Homebrew non installato sulla macchina target: installare prima brew (https://brew.sh) e riprovare")

    _run_step(executor, job_id, "brew install llama.cpp", "Installazione di llama.cpp con Homebrew", timeout=1200)

    # Individua l'eseguibile
    fr = executor.run("command -v llama-server")
    exe_path = fr.stdout.strip().splitlines()[-1] if fr.stdout.strip() else ""
    if not exe_path:
        raise RuntimeError("Installazione completata ma llama-server non trovato, controllare l'output di brew")
    _append_log(job_id, f"  Percorso del motore: {exe_path}")
    return exe_path


def _install_linux(executor: Executor, job_id: str, target: Target) -> str:
    """Compila llama.cpp dai sorgenti (CUDA / ROCm / Vulkan / CPU secondo il backend scelto)"""
    _run_step(executor, job_id,
              "command -v cmake && command -v git && command -v g++",
              "Verifica delle dipendenze di compilazione (cmake/git/g++)", timeout=30)

    build_dir = "/tmp/llama.cpp"
    _run_step(executor, job_id,
              f"rm -rf {build_dir} && git clone --depth 1 https://github.com/ggml-org/llama.cpp {build_dir}",
              "Clonazione dei sorgenti di llama.cpp", timeout=600)

    # [2026-10-01 v1.1.0] Backend scelto (cuda/rocm/vulkan/cpu) -> flag cmake / variabili ambiente.
    # Versione precedente (sostituita): solo CUDA se nvcc presente, altrimenti CPU:
    #   cuda = executor.run("command -v nvcc"); cmake_flag = "-DGGML_CUDA=ON" if cuda.stdout else ""
    backend = resolve_llama_backend(executor, target)
    _append_log(job_id, f"▶ Backend llama.cpp selezionato: {backend.upper()} "
                        f"(impostazione: {getattr(target, 'llama_backend', 'auto')})")
    env_prefix = ""
    if backend == "cuda":
        if not executor.run("command -v nvcc").stdout:
            raise RuntimeError("Backend CUDA richiesto ma nvcc non trovato: installare il CUDA Toolkit "
                               "oppure scegliere Vulkan/CPU nelle Impostazioni")
        cmake_flag = "-DGGML_CUDA=ON"
    elif backend == "vulkan":
        # Servono libvulkan-dev e glslc (pacchetti: libvulkan-dev, glslc / shaderc / vulkan-sdk)
        if not executor.run("command -v glslc").stdout:
            raise RuntimeError("Backend Vulkan: compilatore shader 'glslc' non trovato. Installare "
                               "libvulkan-dev e glslc (Debian/Ubuntu: apt install libvulkan-dev glslc)")
        cmake_flag = "-DGGML_VULKAN=ON"
    elif backend == "rocm":
        hip = executor.run("command -v hipconfig")
        if not hip.stdout:
            raise RuntimeError("Backend ROCm: hipconfig non trovato. Installare ROCm >= 6.4 "
                               "(necessario per RX 9070 XT / gfx1201) oppure scegliere Vulkan")
        # Architettura GPU: es. gfx1201 per RX 9070 XT; se non rilevabile lascia decidere a cmake
        gfx = executor.run("rocminfo 2>/dev/null | grep -m1 -o 'gfx[0-9a-f]\\+'").stdout.strip()
        _append_log(job_id, f"  Architettura GPU rilevata: {gfx or 'non rilevata (uso default cmake)'}")
        env_prefix = 'HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)" '
        cmake_flag = "-DGGML_HIP=ON" + (f" -DAMDGPU_TARGETS={gfx}" if gfx else "")
    else:
        cmake_flag = ""
    _run_step(executor, job_id,
              f"cd {build_dir} && {env_prefix}cmake -B build {cmake_flag} -DCMAKE_BUILD_TYPE=Release "
              f"&& cmake --build build --config Release -j --target llama-server",
              "Compilazione di llama-server" + f" ({backend.upper()})", timeout=2400)

    exe_path = f"{build_dir}/build/bin/llama-server"
    check = executor.run(f'test -f "{exe_path}" && echo FOUND')
    if "FOUND" not in check.stdout:
        raise RuntimeError("Compilazione terminata ma llama-server non e' stato generato")
    _append_log(job_id, f"  Percorso del motore: {exe_path}")
    return exe_path


def _install_vllm(executor: Executor, job_id: str, target: Target) -> str:
    """Installa vLLM con pip (solo Linux/macOS, Windows non supporta l'esecuzione nativa)"""
    if target.os == "windows":
        raise RuntimeError(
            "vLLM non supporta l'esecuzione nativa su Windows: installarlo in WSL2 (Linux) oppure usare llama.cpp")

    _run_step(executor, job_id, "command -v pip3 || command -v pip",
              "Verifica della disponibilita' di pip", timeout=20)

    # Rileva CUDA / GPU NVIDIA (vLLM e' pensato soprattutto per NVIDIA)
    cuda = executor.run(
        "command -v nvcc && nvidia-smi --query-gpu=name --format=csv,noheader", timeout=15)
    if cuda.stdout.strip():
        _append_log(job_id, f"  GPU/CUDA rilevata: {cuda.stdout.strip().splitlines()[0]}")
    else:
        _append_log(job_id, "  CUDA non rilevato: vLLM e' pensato soprattutto per GPU NVIDIA, dopo l'installazione potrebbe non funzionare correttamente")

    _run_step(executor, job_id,
              "pip3 install -U vllm 2>&1 | tail -20 || pip install -U vllm 2>&1 | tail -20",
              "Installazione di vLLM con pip (pacchetto grande e lungo, attendere)", timeout=3600)

    fr = executor.run("command -v vllm")
    exe_path = fr.stdout.strip().splitlines()[-1] if fr.stdout.strip() else ""
    if not exe_path:
        raise RuntimeError("Installazione completata ma comando vllm non trovato, controllare l'output di pip o il PATH")
    _append_log(job_id, f"  Percorso del motore: {exe_path}")
    return exe_path


def _install_comfyui(executor: Executor, job_id: str, target: Target) -> str:
    """git clone di ComfyUI + installazione delle dipendenze con pip. Restituisce la cartella radice di installazione (riportata in engine_path).

    Cartella di installazione: ha priorita' l'engine_path gia' configurato dall'utente come cartella di destinazione, altrimenti una posizione di default generica
    (Windows: C:\\ComfyUI, tipo Unix: ~/ComfyUI). Nessun percorso di macchine personali cablato nel codice."""
    if target.engine_path:
        install_dir = target.engine_path
    elif target.os == "windows":
        install_dir = r"C:\ComfyUI"
    else:
        install_dir = "$HOME/ComfyUI"

    repo = "https://github.com/comfyanonymous/ComfyUI.git"

    # 1) Verifica di git / python / pip
    _run_step(executor, job_id,
              "git --version && (python --version || python3 --version)",
              "Verifica di git e python", timeout=30)

    # 2) Clonazione (se la cartella esiste gia' salta la clonazione, aggiorna soltanto)
    if target.os == "windows":
        exist = executor.run(f'if exist "{install_dir}\\main.py" (echo FOUND)').stdout
        if "FOUND" in exist:
            _append_log(job_id, "  ComfyUI gia' presente, clonazione saltata")
        else:
            _run_step(executor, job_id,
                      f'git clone --depth 1 {repo} "{install_dir}"',
                      "Clonazione del repository di ComfyUI", timeout=900)
    else:
        exist = executor.run(f'test -f {install_dir}/main.py && echo FOUND').stdout
        if "FOUND" in exist:
            _append_log(job_id, "  ComfyUI gia' presente, clonazione saltata")
        else:
            _run_step(executor, job_id,
                      f"git clone --depth 1 {repo} {install_dir}",
                      "Clonazione del repository di ComfyUI", timeout=900)

    # 3) Installazione delle dipendenze con pip (dipendenze grandi come torch, richiede tempo)
    py = "python" if target.os == "windows" else "python3"
    req = _join(install_dir, "requirements.txt") if target.os == "windows" else f"{install_dir.rstrip('/')}/requirements.txt"
    _run_step(executor, job_id,
              f'cd "{install_dir}" && {py} -m pip install -r "{req}" 2>&1 | tail -20'
              if target.os == "windows" else
              f"cd {install_dir} && {py} -m pip install -r requirements.txt 2>&1 | tail -20",
              "Installazione delle dipendenze di ComfyUI con pip (include PyTorch, pacchetto grande e lungo, attendere)", timeout=3600)

    # 4) Verifica del punto di ingresso
    if target.os == "windows":
        chk = executor.run(f'if exist "{install_dir}\\main.py" (echo FOUND)')
    else:
        chk = executor.run(f'test -f {install_dir}/main.py && echo FOUND')
    if "FOUND" not in chk.stdout:
        raise RuntimeError("Installazione completata ma main.py di ComfyUI non trovato, controllare clonazione/rete")
    _append_log(job_id, f"  Cartella di ComfyUI: {install_dir}")
    return install_dir


# ==================== Esecuzione in background ====================

def start_install(target: Target) -> str:
    """Avvia il task di installazione, restituisce job_id"""
    job_id = uuid.uuid4().hex[:8]
    with _LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "status": "running",
            "logs": [],
            "target_id": target.id,
            "engine_path": "",
            "error": "",
        }

    def _worker():
        executor = None
        try:
            from .executor import make_executor
            executor = make_executor(target)
            engine_type = getattr(target, "engine_type", "llama_cpp") or "llama_cpp"
            if engine_type == "vllm":
                _append_log(job_id, f"Avvio dell'installazione di vLLM per «{target.name}» ({target.os})")
                exe = _install_vllm(executor, job_id, target)
            elif engine_type == "sglang":
                _append_log(job_id, f"Avvio dell'installazione di SGLang per «{target.name}» ({target.os})")
                exe = _install_sglang(executor, job_id, target)
            elif engine_type == "comfyui":
                _append_log(job_id, f"Avvio dell'installazione di ComfyUI per «{target.name}» ({target.os})")
                exe = _install_comfyui(executor, job_id, target)
            else:
                _append_log(job_id, f"Avvio dell'installazione di llama.cpp per «{target.name}» ({target.os})")
                if target.os == "windows":
                    exe = _install_windows(executor, job_id, target)
                elif target.os == "macos":
                    exe = _install_macos(executor, job_id, target)
                else:
                    exe = _install_linux(executor, job_id, target)

            # Riporta engine_path nella configurazione
            target.engine_path = exe
            from ..models.target import upsert_target
            upsert_target(target)

            with _LOCK:
                job = _JOBS[job_id]
                job["status"] = "success"
                job["engine_path"] = exe
            _append_log(job_id, "✓ Installazione completata, percorso del motore riportato automaticamente nella configurazione")
        except Exception as e:
            with _LOCK:
                job = _JOBS[job_id]
                job["status"] = "failed"
                job["error"] = str(e)
            _append_log(job_id, f"✗ Installazione non riuscita: {e}")
        finally:
            if executor:
                executor.close()

    threading.Thread(target=_worker, daemon=True).start()
    return job_id
