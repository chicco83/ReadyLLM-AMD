"""Raccolta dei dati hardware e di monitoraggio

Ogni raccolta viene eseguita sul Target configurato dall'utente, adattando i comandi al sistema operativo di destinazione (windows/macos/linux),
senza dipendere da alcun ambiente cablato nel codice.
"""

import json

from .executor import Executor
from ..models.target import Target


def path_join(target: Target, *parts: str) -> str:
    """Concatena i percorsi secondo il sistema operativo di destinazione"""
    sep = "\\" if target.os == "windows" else "/"
    base = parts[0].rstrip("\\/")
    return base + sep + sep.join(p.strip("\\/") for p in parts[1:])


def _parse_kv_lines(text: str) -> dict:
    """Interpreta l'output in forma key=value"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _ps(body: str) -> str:
    """Racchiude il corpo in powershell -NoProfile -Command "...".
    Le versioni recenti di Windows hanno rimosso wmic, quindi si usa ovunque Get-CimInstance (disponibile da Win8+/PS3+).
    Nel corpo si usano solo apici singoli e variabili $, per evitare conflitti con le virgolette doppie esterne."""
    return f'powershell -NoProfile -Command "{body}"'


# ==================== GPU: rilevamento vendor-agnostico (v1.1.0, 2026-10-01) ====================
# CAUSA DEL BUG "Radeon RX 9070 XT non rilevata": prima il rilevamento GPU provava SOLO
# nvidia-smi (e system_profiler su macOS). Su un PC con scheda AMD nvidia-smi non esiste,
# quindi _detect_gpu_static restituiva None e _collect_gpu un dict vuoto: nessuna GPU in
# Impostazioni/Monitor e nessuna raccomandazione di modelli per la VRAM.
# Ora: NVIDIA -> nvidia-smi; AMD/altre su Windows -> registro di sistema (qwMemorySize,
# perche' Win32_VideoController.AdapterRAM e' un uint32 e satura a 4 GB: una scheda da 16 GB
# risulterebbe da 4 GB); AMD su Linux -> sysfs amdgpu + lspci (+ rocminfo per l'architettura gfx).

_GPU_STATIC_CACHE: dict = {}   # target.id -> info statica (nome/VRAM totale), evita query a ogni ciclo di monitor

# Adattatori virtuali/di servizio da ignorare nell'elenco GPU Windows
_VIRTUAL_GPU_HINTS = ("basic render", "basic display", "remote", "virtual", "parsec", "meta ",
                      "displaylink", "indirect", "mirage")


def _gpu_vendor(name: str) -> str:
    """Deduce il vendor dal nome commerciale della scheda."""
    n = (name or "").lower()
    if any(k in n for k in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla")):
        return "nvidia"
    if any(k in n for k in ("amd", "radeon", "ati ", "navi", "vega", "instinct")):
        return "amd"
    if any(k in n for k in ("intel", "arc ", "uhd", "iris")):
        return "intel"
    if "apple" in n:
        return "apple"
    return "unknown"


def _parse_qword(raw: str) -> int:
    """Il registro puo' restituire qwMemorySize come intero oppure come array di byte
    little-endian ("0 0 0 64 4 0 0 0"): gestisce entrambi i formati."""
    raw = (raw or "").strip()
    if not raw:
        return 0
    try:
        if " " in raw:
            return int.from_bytes(bytes(int(x) for x in raw.split()), "little")
        return int(float(raw))
    except (ValueError, OverflowError):
        return 0


def _fmt_driver(v: str) -> str:
    """[2026-10-01 v1.1.7] DirectX registra DriverVersion come uint64 impacchettato (4 word da 16 bit):
    es. 9007201288716303 -> 32.0.21013.xxxx. Se non e' un numero cosi' grande lo restituisce invariato."""
    v = (v or "").strip()
    if v.isdigit() and len(v) > 10:
        n = int(v)
        return ".".join(str((n >> sh) & 0xFFFF) for sh in (48, 32, 16, 0))
    return v


def _detect_gpu_windows(executor: Executor) -> dict:
    """Elenca le GPU Windows dal registro (classe display) e sceglie la piu' capiente.
    Con una iGPU + una dGPU (es. Ryzen + RX 9070 XT) vince la dGPU per quantita' di VRAM."""
    cmd = _ps(
        "$c='HKLM:\\SYSTEM\\ControlSet001\\Control\\Class\\{4d36e968-e325-11cd-bfc1-08002be10318}\\0*'; "
        "Get-ItemProperty $c -ErrorAction SilentlyContinue | ForEach-Object { "
        "Write-Output ('GPU=' + $_.DriverDesc + '|' + $_.'HardwareInformation.qwMemorySize' + '|' + $_.DriverVersion) }; "
        # [2026-10-01 v1.1.6] Seconda fonte a 64 bit: HKLM\\SOFTWARE\\Microsoft\\DirectX\\<guid> (Description +
        # DedicatedVideoMemory), scritta da Windows per ogni adattatore. Serve quando la chiave della classe display
        # non espone qwMemorySize: senza, restava solo Win32_VideoController.AdapterRAM (uint32, max 4 GB).
        "Get-ChildItem 'HKLM:\\SOFTWARE\\Microsoft\\DirectX' -ErrorAction SilentlyContinue | ForEach-Object { "
        "$p=Get-ItemProperty $_.PSPath; if ($p.Description) { "
        "Write-Output ('GPU=' + $p.Description + '|' + $p.DedicatedVideoMemory + '|' + $p.DriverVersion) } }; "
        "Get-CimInstance Win32_VideoController | ForEach-Object { "
        "Write-Output ('VC=' + $_.Name + '|' + $_.AdapterRAM + '|' + $_.DriverVersion) }"
    )
    result = executor.run(cmd, timeout=25)
    cards = []
    vc_names = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not (line.startswith("GPU=") or line.startswith("VC=")):
            continue
        kind, rest = line.split("=", 1)
        parts = (rest.split("|") + ["", "", ""])[:3]
        name, mem, drv = parts[0].strip(), _parse_qword(parts[1]), parts[2].strip()
        if not name or any(h in name.lower() for h in _VIRTUAL_GPU_HINTS):
            continue
        if kind == "VC":
            vc_names.append((name, mem, drv))   # AdapterRAM: solo ripiego (max 4 GB)
        else:
            cards.append((name, mem, drv))
    if not cards:
        cards = vc_names
    if not cards:
        return {}
    name, mem, drv = max(cards, key=lambda c: c[1])
    return {
        "name": name,
        "vendor": _gpu_vendor(name),
        "total_memory_gb": round(mem / 1024**3, 1),
        "free_memory_gb": 0,           # il registro non espone la VRAM libera
        "driver": _fmt_driver(drv),
    }


_LINUX_AMD_SCAN = (
    "for d in /sys/class/drm/card[0-9]*/device; do "
    "[ \"$(cat $d/vendor 2>/dev/null)\" = \"0x1002\" ] || continue; "
    "echo \"CARD|$(basename $(readlink -f $d))|$(cat $d/mem_info_vram_total 2>/dev/null)"
    "|$(cat $d/mem_info_vram_used 2>/dev/null)|$(cat $d/gpu_busy_percent 2>/dev/null)"
    "|$(cat $d/hwmon/hwmon*/temp1_input 2>/dev/null | head -1)"
    "|$(cat $d/hwmon/hwmon*/power1_average $d/hwmon/hwmon*/power1_input 2>/dev/null | head -1)\"; "
    "done"
)


def _linux_amd_cards(executor: Executor) -> list[dict]:
    """Legge le schede AMD da sysfs (driver amdgpu). Una riga per scheda, dedotta da PCI id."""
    result = executor.run(_LINUX_AMD_SCAN, timeout=10)
    cards, seen = [], set()
    for line in (result.stdout or "").splitlines():
        p = line.strip().split("|")
        if len(p) < 7 or p[0] != "CARD" or p[1] in seen:
            continue
        seen.add(p[1])

        def num(x):
            try:
                return float(x)
            except ValueError:
                return 0.0
        cards.append({"pci": p[1], "vram_total": num(p[2]), "vram_used": num(p[3]),
                      "busy": num(p[4]), "temp_mc": num(p[5]), "power_uw": num(p[6])})
    return cards


def _detect_gpu_linux_amd(executor: Executor) -> dict:
    cards = _linux_amd_cards(executor)
    if not cards:
        return {}
    c = max(cards, key=lambda x: x["vram_total"])   # dGPU > iGPU per VRAM
    pci = c["pci"].split(":", 1)[-1] if c["pci"].count(":") > 1 else c["pci"]
    # Nome commerciale: lspci -> "... [AMD/ATI] Navi 48 [Radeon RX 9070/9070 XT/9070 GRE] (rev c0)"
    r = executor.run(f"lspci -s {pci} 2>/dev/null | sed 's/^[^ ]* [^:]*: //'", timeout=8)
    name = (r.stdout or "").strip() or "AMD GPU"
    # Architettura gfx (es. gfx1201 = RDNA4 / RX 9070 XT) se ROCm e' installato
    g = executor.run("rocminfo 2>/dev/null | grep -m1 -o 'gfx[0-9a-f]\\+'", timeout=10)
    d = executor.run("cat /sys/module/amdgpu/version 2>/dev/null || uname -r", timeout=5)
    info = {
        "name": name,
        "vendor": "amd",
        "total_memory_gb": round(c["vram_total"] / 1024**3, 1),
        "free_memory_gb": round(max(c["vram_total"] - c["vram_used"], 0) / 1024**3, 1),
        "driver": (d.stdout or "").strip() or "amdgpu",
    }
    gfx = (g.stdout or "").strip()
    if gfx:
        info["gfx"] = gfx
    return info


def _collect_gpu_amd_linux(executor: Executor) -> dict:
    """Monitor realtime AMD su Linux via sysfs (nessuna dipendenza da rocm-smi)."""
    cards = _linux_amd_cards(executor)
    if not cards:
        return {}
    c = max(cards, key=lambda x: x["vram_total"])
    total = c["vram_total"]
    name = _static_gpu_cached(executor, None, "linux").get("name", "AMD GPU")
    return {
        "name": name,
        "utilization": c["busy"],
        "memory_used_gb": round(c["vram_used"] / 1024**3, 1),
        "memory_total_gb": round(total / 1024**3, 1),
        "memory_pct": round(c["vram_used"] / total * 100, 1) if total > 0 else 0,
        "temperature": int(c["temp_mc"] / 1000),     # millesimi di grado -> gradi
        "power": round(c["power_uw"] / 1_000_000, 1),  # microwatt -> watt
    }


# [2026-10-01 v1.1.21] Temperatura GPU su Windows: stessa sorgente di Gestione attivita' (D3DKMTQueryAdapterInfo,
# KMTQAITYPE_ADAPTERPERFDATA = 62, gdi32.dll). Windows non la espone via WMI/contatori. Il LUID dell'adapter si ricava dai nomi
# dei contatori GPU (luid_0x<alto>_0x<basso>_phys_0): si sceglie quello con piu' memoria dedicata in uso (la GPU discreta).
# Lo script C# e' passato con -EncodedCommand (base64 UTF-16LE) per evitare problemi di virgolette. Il risultato e' in cache
# 5 s (Add-Type costa ~1 s); se non disponibile (driver senza supporto) restituisce 0 e non si riprova per 10 minuti.
_PS_TEMP_SCRIPT = r"""
$ErrorActionPreference='Stop'
try {
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public static class GpuT {
 [StructLayout(LayoutKind.Sequential)] public struct O { public uint Lo; public int Hi; public uint H; }
 [StructLayout(LayoutKind.Sequential)] public struct Q { public uint H; public uint T; public IntPtr D; public uint S; }
 [StructLayout(LayoutKind.Sequential)] public struct C { public uint H; }
 [StructLayout(LayoutKind.Sequential, Pack=8)] public struct P { public uint I; public ulong A; public ulong B; public ulong C; public ulong D; public ulong E; public uint Fan; public uint Pw; public uint Tmp; public byte X; }
 [DllImport("gdi32.dll")] static extern int D3DKMTOpenAdapterFromLuid(ref O o);
 [DllImport("gdi32.dll")] static extern int D3DKMTQueryAdapterInfo(ref Q q);
 [DllImport("gdi32.dll")] static extern int D3DKMTCloseAdapter(ref C c);
 public static string Run(uint lo, int hi) {
  O o = new O(); o.Lo = lo; o.Hi = hi;
  int r = D3DKMTOpenAdapterFromLuid(ref o); if (r != 0) return "open=" + r;
  IntPtr b = Marshal.AllocHGlobal(256); string res;
  try { Q q = new Q(); q.H = o.H; q.T = 62; q.D = b; q.S = (uint)Marshal.SizeOf(typeof(P));
   r = D3DKMTQueryAdapterInfo(ref q); if (r != 0) res = "query=" + r;
   else { P p = (P)Marshal.PtrToStructure(b, typeof(P)); res = "T=" + p.Tmp; } }
  finally { Marshal.FreeHGlobal(b); C c = new C(); c.H = o.H; D3DKMTCloseAdapter(ref c); }
  return res; }
}
'@
$m=Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory | Sort-Object DedicatedUsage -Descending | Select-Object -First 1
if($m.Name -match 'luid_0x([0-9a-fA-F]+)_0x([0-9a-fA-F]+)'){
 $r=[GpuT]::Run([Convert]::ToUInt32($matches[2],16),[Convert]::ToInt32($matches[1],16))
 if($r -match '^T=(\d+)$' -and [int]$matches[1] -gt 0){ Write-Output ('GPUTEMP=' + [math]::Round([int]$matches[1]/10.0,1)) } else { Write-Output ('GPUTEMPERR=' + $r) }
} else { Write-Output 'GPUTEMPERR=luid-non-trovato' }
} catch { Write-Output ('GPUTEMPERR=' + $_.Exception.Message) }
"""
_TEMP_CACHE: dict = {}   # chiave target -> (timestamp, valore) ; valore None = non disponibile


def _gpu_temp_windows(executor: Executor, target: Target) -> float:
    """Temperatura GPU in gradi C come Gestione attivita' (0 se non disponibile). Vedi nota v1.1.21."""
    import base64, time as _t
    key = getattr(target, "id", None) or "local"
    ts, val = _TEMP_CACHE.get(key, (0, 0.0))
    ttl = 600 if val is None else 5
    if _t.time() - ts < ttl:
        return val or 0.0
    enc = base64.b64encode(_PS_TEMP_SCRIPT.encode("utf-16-le")).decode()
    r = executor.run(f"powershell -NoProfile -EncodedCommand {enc}", timeout=25)
    kv = _parse_kv_lines(r.stdout)
    try:
        val = float(kv.get("GPUTEMP"))
    except (TypeError, ValueError):
        val = None
        # [2026-10-02 v1.1.23] il motivo del fallimento finisce nella console del backend (prima era silenzioso)
        import logging
        logging.getLogger("uvicorn.error").warning("Temperatura GPU non disponibile: %s | %s",
                                                    kv.get("GPUTEMPERR", "nessun output"), (r.stderr or "")[:300])
    _TEMP_CACHE[key] = (_t.time(), val)
    return val or 0.0


def _collect_gpu_windows(executor: Executor, target: Target) -> dict:
    """Monitor realtime su Windows per qualsiasi vendor (AMD/Intel/NVIDIA senza nvidia-smi).
    Usa le classi WMI Win32_PerfFormattedData_GPUPerformanceCounters_*: i NOMI DI CLASSE non
    sono localizzati (a differenza dei percorsi Get-Counter, che su Windows in italiano
    sarebbero 'Motore GPU' ecc.). Temperatura/potenza non sono esposte da Windows -> 0."""
    # [2026-10-01 v1.1.18] Utilizzo GPU dai contatori RAW (due campioni a 0.7 s, delta busy/delta tempo): le classi
    # «Formatted» davano 0% perche' il primo campione dopo una pausa non ha un delta valido (GPU 0.0% nel tuning anche con la
    # GPU al lavoro a 47 t/s). Si somma per tipo di engine (3D, Compute_0, Copy...) e si prende il piu' carico.
    # Versione precedente: Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -> Measure-Object UtilizationPercentage -Sum
    cmd = _ps(
        "$a=Get-CimInstance Win32_PerfRawData_GPUPerformanceCounters_GPUEngine -ErrorAction SilentlyContinue; "
        "Start-Sleep -Milliseconds 700; "
        "$b=Get-CimInstance Win32_PerfRawData_GPUPerformanceCounters_GPUEngine -ErrorAction SilentlyContinue; "
        "$h=@{}; foreach($x in $a){$h[$x.Name]=$x}; $g=@{}; "
        "foreach($y in $b){ $x=$h[$y.Name]; if($x){ $dt=$y.Timestamp_Sys100NS-$x.Timestamp_Sys100NS; "
        "if($dt -gt 0){ $k=($y.Name -replace '.*engtype_',''); $g[$k]+=(($y.UtilizationPercentage-$x.UtilizationPercentage)/$dt*100) } } }; "
        "$m=0; foreach($k in $g.Keys){ if($g[$k] -gt $m){$m=$g[$k]} }; "
        "$mem=Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory -ErrorAction SilentlyContinue "
        "| Measure-Object DedicatedUsage -Maximum; "
        "Write-Output ('UTIL=' + [math]::Round($m,1)); Write-Output ('VRAMUSED=' + $mem.Maximum)"
    )
    result = executor.run(cmd, timeout=25)
    kv = _parse_kv_lines(result.stdout)
    static = _static_gpu_cached(executor, target, "windows")
    if not static:
        return {}
    try:
        util = min(float(kv.get("UTIL") or 0), 100.0)   # somma dei processi: limitata a 100%
        used = float(kv.get("VRAMUSED") or 0)
    except ValueError:
        util, used = 0.0, 0.0
    total = static.get("total_memory_gb", 0) * 1024**3
    return {
        "name": static.get("name", ""),
        "utilization": round(util, 1),
        "memory_used_gb": round(used / 1024**3, 1),
        "memory_total_gb": static.get("total_memory_gb", 0),
        "memory_pct": round(used / total * 100, 1) if total > 0 else 0,
        # [2026-10-01 v1.1.21] prima: "temperature": 0 (non esposta da Windows); ora da D3DKMT come Gestione attivita'
        "temperature": round(_gpu_temp_windows(executor, target)),
        "power": 0,
    }


def _static_gpu_cached(executor: Executor, target, os_name: str) -> dict:
    """Info statica GPU (nome/VRAM) con cache: serve al monitor per nome e VRAM totale."""
    key = getattr(target, "id", None) or os_name
    if key not in _GPU_STATIC_CACHE:
        info = _detect_gpu_windows(executor) if os_name == "windows" else _detect_gpu_linux_amd(executor)
        if info:
            _GPU_STATIC_CACHE[key] = info
        return info
    return _GPU_STATIC_CACHE[key]


# ==================== GPU ====================

def _collect_gpu(executor: Executor, target: Target) -> dict:
    """Raccolta GPU in tempo reale: NVIDIA con nvidia-smi; per Apple Silicon powermetrics non e' praticabile, restituisce vuoto"""
    cmd = ("nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,"
           "temperature.gpu,power.draw --format=csv,noheader,nounits")
    result = executor.run(cmd, timeout=8)
    if result.ok and result.stdout:
        parts = [p.strip() for p in result.stdout.split(",")]
        if len(parts) >= 6:
            name, util, mem_used, mem_total, temp, power = parts[:6]
            try:
                mem_used_f, mem_total_f = float(mem_used), float(mem_total)
                return {
                    "name": name,
                    "utilization": float(util),
                    "memory_used_gb": round(mem_used_f / 1024, 1),
                    "memory_total_gb": round(mem_total_f / 1024, 1),
                    "memory_pct": round(mem_used_f / mem_total_f * 100, 1) if mem_total_f > 0 else 0,
                    "temperature": int(float(temp)),
                    "power": float(power),
                }
            except ValueError:
                pass
    # [2026-10-01 v1.1.0] Nessuna NVIDIA: prova AMD (Linux sysfs) / qualsiasi vendor (Windows WMI).
    # Prima qui si restituiva sempre {} -> Monitor senza dati su schede Radeon.
    if target.os == "windows":
        return _collect_gpu_windows(executor, target)
    if target.os == "linux":
        return _collect_gpu_amd_linux(executor)
    return {}


# ==================== CPU / Memoria ====================

def _collect_cpu_mem(executor: Executor, target: Target) -> dict:
    if target.os == "windows":
        cmd = _ps(
            "$c=(Get-CimInstance Win32_Processor|Measure-Object LoadPercentage -Average).Average; "
            "$o=Get-CimInstance Win32_OperatingSystem; "
            "Write-Output ('LoadPercentage=' + $c); "
            "Write-Output ('TotalVisibleMemorySize=' + $o.TotalVisibleMemorySize); "
            "Write-Output ('FreePhysicalMemory=' + $o.FreePhysicalMemory)"
        )
        result = executor.run(cmd, timeout=20)
        kv = _parse_kv_lines(result.stdout)
        try:
            total = float(kv.get("TotalVisibleMemorySize", 0))
            free = float(kv.get("FreePhysicalMemory", 0))
            if total > 0:
                return {
                    "cpu_pct": float(kv.get("LoadPercentage", 0)),
                    "memory_used_gb": round((total - free) / 1024 / 1024, 1),
                    "memory_total_gb": round(total / 1024 / 1024, 1),
                    "memory_pct": round((total - free) / total * 100, 1),
                }
        except ValueError:
            pass
        return {}

    elif target.os == "macos":
        # La shell stampa solo dati grezzi, l'analisi con regex avviene in Python, evitando i problemi di virgolette di awk
        import re
        cmd = (
            "top -l 1 | grep 'CPU usage'; "
            "vm_stat; "
            "echo MEMTOTAL=$(sysctl -n hw.memsize)"
        )
        result = executor.run(cmd, timeout=15)
        text = result.stdout
        try:
            idle = 100.0
            page_size = 4096
            active = wired = compressed = 0
            total = 0.0
            for line in text.splitlines():
                if "CPU usage" in line and "idle" in line:
                    nums = re.findall(r"([0-9.]+)%", line)
                    if nums:
                        idle = float(nums[-1])
                elif "page size of" in line:
                    m = re.findall(r"(\d+)", line)
                    if m:
                        page_size = int(m[0])
                elif line.startswith("Pages active"):
                    m = re.findall(r"(\d+)", line)
                    if m:
                        active = int(m[-1])
                elif line.startswith("Pages wired down"):
                    m = re.findall(r"(\d+)", line)
                    if m:
                        wired = int(m[-1])
                elif line.startswith("Pages occupied by compressor"):
                    m = re.findall(r"(\d+)", line)
                    if m:
                        compressed = int(m[-1])
                elif line.startswith("MEMTOTAL="):
                    m = re.findall(r"(\d+)", line)
                    if m:
                        total = float(m[-1])
            if total > 0:
                used_bytes = (active + wired + compressed) * page_size
                return {
                    "cpu_pct": round(100 - idle, 1),
                    "memory_used_gb": round(used_bytes / 1024**3, 1),
                    "memory_total_gb": round(total / 1024**3, 1),
                    "memory_pct": round(used_bytes / total * 100, 1),
                }
        except (ValueError, IndexError):
            pass
        return {}

    else:  # linux
        cmd = ("echo CPU=$(vmstat 1 2 | tail -1 | awk '{print 100-$15}'); "
               "free -b | awk '/Mem:/{print \"MEMTOTAL=\"$2; print \"MEMAVAIL=\"$7}'")
        result = executor.run(cmd, timeout=12)
        kv = _parse_kv_lines(result.stdout)
        try:
            total = float(kv.get("MEMTOTAL", 0))
            avail = float(kv.get("MEMAVAIL", 0))
            if total > 0:
                return {
                    "cpu_pct": float(kv.get("CPU", 0)),
                    "memory_used_gb": round((total - avail) / 1024 / 1024 / 1024, 1),
                    "memory_total_gb": round(total / 1024 / 1024 / 1024, 1),
                    "memory_pct": round((total - avail) / total * 100, 1),
                }
        except ValueError:
            pass
        return {}


# ==================== Metriche di inferenza ====================

def _collect_vllm_metrics(m: dict) -> dict:
    """Mappatura delle metriche del motore vLLM (formato Prometheus, prefisso vllm:)

    vLLM espone Counter cumulativi e _sum/_count degli histogram, da cui si ricavano campi
    isomorfi a quelli di llama.cpp, cosi' il frontend riusa le stesse curve. vLLM non ha il concetto di
    decodifica speculativa / tasso di hit della cache dei prefissi: i campi corrispondenti valgono 0 (il frontend non li disegna).
    """
    prompt_tokens = m.get("vllm:prompt_tokens_total", 0)
    completion_tokens = m.get("vllm:generation_tokens_total", 0)
    # Somma cumulativa degli istogrammi di durata (secondi)
    prompt_seconds = m.get("vllm:time_to_first_token_seconds_sum", 0)
    e2e_seconds = m.get("vllm:e2e_request_latency_seconds_sum", 0)

    return {
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "prompt_speed": round(prompt_tokens / prompt_seconds, 2) if prompt_seconds > 0 else 0,
        "completion_speed": round(completion_tokens / e2e_seconds, 2) if e2e_seconds > 0 else 0,
        "cache_hit_rate": 0,   # vLLM per ora non espone una metrica equivalente
        "spec_accept_rate": 0,  # le metriche di decodifica speculativa di vLLM hanno un'altra definizione, per ora non raccolte
    }


def _collect_sglang_metrics(m: dict) -> dict:
    """Mappatura delle metriche del motore SGLang (formato Prometheus, prefisso sglang:)

    Secondo la documentazione ufficiale references/production_metrics: avviato con --enable-metrics, SGLang
    espone sglang:prompt_tokens_total / generation_tokens_total (counter),
    time_to_first_token_seconds / e2e_request_latency_seconds（histogram，
    _sum), cache_hit_rate (gauge, valori 0~1).

    Differenza rispetto al ramo vLLM: SGLang fornisce direttamente il tasso di hit della cache dei prefissi RadixAttention,
    quindi cache_hit_rate ha un valore reale (convertito in percentuale come in llama.cpp);
    la definizione della decodifica speculativa e' diversa, restituisce 0. La definizione della velocita' e' la stessa del ramo vLLM.
    """
    prompt_tokens = m.get("sglang:prompt_tokens_total", 0)
    completion_tokens = m.get("sglang:generation_tokens_total", 0)
    # Somma cumulativa degli istogrammi di durata (secondi)
    prompt_seconds = m.get("sglang:time_to_first_token_seconds_sum", 0)
    e2e_seconds = m.get("sglang:e2e_request_latency_seconds_sum", 0)
    # L'esempio della documentazione ufficiale e' un rapporto 0~1; come per llama.cpp il frontend lo mostra in percentuale
    cache_hit = m.get("sglang:cache_hit_rate", 0)

    return {
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "prompt_speed": round(prompt_tokens / prompt_seconds, 2) if prompt_seconds > 0 else 0,
        "completion_speed": round(completion_tokens / e2e_seconds, 2) if e2e_seconds > 0 else 0,
        "cache_hit_rate": round(cache_hit * 100, 1) if cache_hit else 0,
        "spec_accept_rate": 0,  # le metriche di decodifica speculativa di SGLang hanno un'altra definizione, per ora non raccolte
    }


def _metrics_url(target: Target) -> str:
    return f"http://127.0.0.1:{target.service_port}/metrics"


def _comfy_base(target: Target) -> str:
    port = target.service_port or 8188
    return f"http://127.0.0.1:{port}"


def _comfy_curl_json(executor: Executor, target: Target, path: str, timeout: int = 8):
    """Esegue curl su un endpoint JSON di ComfyUI sulla macchina target; in caso di errore restituisce None."""
    url = _comfy_base(target) + path
    cmd = f'curl -s --max-time {timeout} "{url}"'
    result = executor.run(cmd, timeout=timeout + 4)
    out = (result.stdout or "").strip()
    if not out or not out.startswith("{"):
        return None
    try:
        return json.loads(out)
    except (ValueError, json.JSONDecodeError):
        return None


def _collect_comfyui_metrics(executor: Executor, target: Target) -> dict:
    """Panoramica a livello di motore di ComfyUI: stato online + lunghezza della coda + uso della VRAM.

    ComfyUI non ha il concetto di token/cache/campionamento speculativo: i campi restituiti non sono isomorfi a quelli dei motori testuali,
    e il frontend passa alla scheda panoramica video in base al campo engine. L'avanzamento passo-passo di una singola generazione e' fornito
    dall'interfaccia /api/deploy/generate/progress (polling basato su prompt_id)."""
    stats = _comfy_curl_json(executor, target, "/system_stats")
    if stats is None:
        # Il servizio non risponde
        return {"engine": "comfyui", "online": False}

    # /system_stats.devices[] contiene vram_total / vram_free (byte)
    vram_used_gb = vram_total_gb = 0.0
    devices = stats.get("devices") or []
    for dev in devices:
        vt = float(dev.get("vram_total", 0) or 0)
        vf = float(dev.get("vram_free", 0) or 0)
        if vt > 0:
            vram_total_gb = round(vt / 1024**3, 1)
            vram_used_gb = round((vt - vf) / 1024**3, 1)
            break

    running = pending = 0
    q = _comfy_curl_json(executor, target, "/queue")
    if q:
        running = len(q.get("queue_running") or [])
        pending = len(q.get("queue_pending") or [])

    return {
        "engine": "comfyui",
        "online": True,
        "queue_running": running,
        "queue_pending": pending,
        "vram_used_gb": vram_used_gb,
        "vram_total_gb": vram_total_gb,
    }


def collect_metrics(executor: Executor, target: Target) -> dict:
    """Esegue curl sulle metriche della macchina target (la porta delle metriche e' accessibile solo localmente sulla macchina target)"""
    # ComfyUI e' un'API JSON, non testo Prometheus: gestita separatamente
    if getattr(target, "engine_type", "") == "comfyui":
        return _collect_comfyui_metrics(executor, target)

    cmd = f'curl -s --max-time 5 {_metrics_url(target)}'
    result = executor.run(cmd, timeout=8)
    if not result.ok or not result.stdout:
        return {}

    m = {}
    for line in result.stdout.splitlines():
        if line.startswith("#") or " " not in line:
            continue
        parts = line.split(" ")
        if len(parts) >= 2:
            try:
                val = float(parts[1])
            except ValueError:
                continue
            # Le metriche Prometheus possono avere etichette (vLLM / SGLang emettono entrambi {model_name="..."}).
            # Si unificano per nome della metrica senza etichetta, sommando le etichette multiple con lo stesso nome (equivale a prenderne il valore in un deploy a modello singolo),
            # altrimenti in m resterebbe «nome{etichetta}» e la ricerca per nome darebbe sempre 0.
            name = parts[0].split("{", 1)[0]
            m[name] = m.get(name, 0.0) + val

    # Consapevolezza del motore: vLLM / SGLang e llama.cpp hanno prefissi e nomi di metriche diversi
    engine_type = getattr(target, "engine_type", "llama_cpp") or "llama_cpp"
    if engine_type == "vllm":
        return _collect_vllm_metrics(m)
    if engine_type == "sglang":
        return _collect_sglang_metrics(m)

    prompt_tokens = m.get("llamacpp:prompt_tokens_total", 0)
    completion_tokens = m.get("llamacpp:tokens_predicted_total", 0)
    prompt_seconds = m.get("llamacpp:prompt_seconds_total", 0)
    predict_seconds = m.get("llamacpp:tokens_predicted_seconds_total", 0)
    cached_tokens = m.get("llamacpp:prompt_tokens_cached_total", 0)
    # [2026-10-02 v1.1.23] Le build recenti possono chiamare diversamente la metrica dei token riusati dalla cache: se il nome
    # standard manca si cerca un contatore *_total con «cache» e «token» nel nome (esclusi i gauge di occupazione).
    # Nota: se la build non espone nessuna metrica di questo tipo il valore resta 0 (non c'e' dato da cui calcolarlo).
    if not cached_tokens:
        for k, v in m.items():
            if k.startswith("llamacpp:") and "cache" in k and "token" in k and k.endswith("_total") and "kv_cache" not in k:
                cached_tokens = v
                break
    spec_draft = m.get("llamacpp:spec_decode_num_draft_tokens_total", 0)
    spec_accepted = m.get("llamacpp:spec_decode_num_accepted_tokens_total", 0)

    total_prompt = prompt_tokens + cached_tokens
    return {
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "prompt_speed": round(prompt_tokens / prompt_seconds, 2) if prompt_seconds > 0 else 0,
        "completion_speed": round(completion_tokens / predict_seconds, 2) if predict_seconds > 0 else 0,
        "cache_hit_rate": round(cached_tokens / total_prompt * 100, 1) if total_prompt > 0 else 0,
        "spec_accept_rate": round(spec_accepted / spec_draft * 100, 1) if spec_draft > 0 else 0,
    }


# ==================== Rilevamento hardware (per la home) ====================

def _detect_cpu_static(executor: Executor, target: Target) -> dict:
    if target.os == "windows":
        result = executor.run(
            _ps("$p=Get-CimInstance Win32_Processor; "
                "Write-Output ('Name=' + $p.Name); "
                "Write-Output ('NumberOfCores=' + $p.NumberOfCores); "
                "Write-Output ('NumberOfLogicalProcessors=' + $p.NumberOfLogicalProcessors)"),
            timeout=20)
        kv = _parse_kv_lines(result.stdout)
        return {
            "name": kv.get("Name", ""),
            "cores": int(kv.get("NumberOfCores", 0) or 0),
            "threads": int(kv.get("NumberOfLogicalProcessors", 0) or 0),
        }
    elif target.os == "macos":
        cmd = ("echo NAME=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || "
               "sysctl -n hw.model); "
               "echo CORES=$(sysctl -n hw.physicalcpu); "
               "echo THREADS=$(sysctl -n hw.logicalcpu)")
        result = executor.run(cmd, timeout=10)
        kv = _parse_kv_lines(result.stdout)
        return {
            "name": kv.get("NAME", ""),
            "cores": int(kv.get("CORES", 0) or 0),
            "threads": int(kv.get("THREADS", 0) or 0),
        }
    else:  # linux
        cmd = ("echo NAME=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//'); "
               "echo CORES=$(grep -c ^processor /proc/cpuinfo)")
        result = executor.run(cmd, timeout=10)
        kv = _parse_kv_lines(result.stdout)
        cores = int(kv.get("CORES", 0) or 0)
        return {"name": kv.get("NAME", ""), "cores": cores, "threads": cores}


def _detect_memory_static(executor: Executor, target: Target) -> dict:
    if target.os == "windows":
        result = executor.run(
            _ps("$o=Get-CimInstance Win32_OperatingSystem; "
                "Write-Output ('TotalVisibleMemorySize=' + $o.TotalVisibleMemorySize); "
                "Write-Output ('FreePhysicalMemory=' + $o.FreePhysicalMemory)"),
            timeout=20)
        kv = _parse_kv_lines(result.stdout)
        total = float(kv.get("TotalVisibleMemorySize", 0) or 0)
        free = float(kv.get("FreePhysicalMemory", 0) or 0)
        return {"total_gb": round(total / 1024 / 1024, 1), "free_gb": round(free / 1024 / 1024, 1)}
    elif target.os == "macos":
        result = executor.run("echo TOTAL=$(sysctl -n hw.memsize)", timeout=10)
        kv = _parse_kv_lines(result.stdout)
        total = float(kv.get("TOTAL", 0) or 0)
        return {"total_gb": round(total / 1024**3, 1), "free_gb": 0}
    else:  # linux
        result = executor.run("free -b | awk '/Mem:/{print \"TOTAL=\"$2; print \"AVAIL=\"$7}'", timeout=10)
        kv = _parse_kv_lines(result.stdout)
        total = float(kv.get("TOTAL", 0) or 0)
        avail = float(kv.get("AVAIL", 0) or 0)
        return {"total_gb": round(total / 1024**3, 1), "free_gb": round(avail / 1024**3, 1)}


def _detect_gpu_static(executor: Executor, target: Target) -> dict:
    # NVIDIA, multipiattaforma
    result = executor.run(
        "nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version "
        "--format=csv,noheader,nounits", timeout=8)
    if result.ok and result.stdout:
        parts = [p.strip() for p in result.stdout.split(",")]
        if len(parts) >= 4:
            try:
                return {
                    "name": parts[0],
                    "vendor": "nvidia",
                    "total_memory_gb": round(float(parts[1]) / 1024, 1),
                    "free_memory_gb": round(float(parts[2]) / 1024, 1),
                    "driver": parts[3],
                }
            except ValueError:
                pass

    # macOS Apple Silicon: memoria unificata, nessuna VRAM dedicata, restituisce le informazioni della GPU del chip
    if target.os == "macos":
        result = executor.run(
            "system_profiler SPDisplaysDataType 2>/dev/null | "
            "awk '/Chipset Model/{gsub(/^[ \t]+Chipset Model: /,\"\"); name=name\" \"$0} "
            "/Vendor/{next} END{print \"NAME=\"name}'", timeout=12)
        kv = _parse_kv_lines(result.stdout)
        name = kv.get("NAME", "").strip()
        if name:
            return {"name": name, "vendor": "apple", "total_memory_gb": 0, "free_memory_gb": 0,
                    "driver": "Apple", "unified": True}

    # [2026-10-01 v1.1.0] AMD / altri vendor (vedi spiegazione sopra, "CAUSA DEL BUG").
    if target.os == "windows":
        info = _detect_gpu_windows(executor)
    else:
        info = _detect_gpu_linux_amd(executor)
    return info or None


def _detect_disk(executor: Executor, target: Target) -> dict:
    if target.os == "windows":
        drive = target.models_dir[:2] if len(target.models_dir) >= 2 else "C:"
        result = executor.run(
            _ps(f"$d=Get-CimInstance Win32_LogicalDisk | Where-Object DeviceID -eq '{drive}'; "
                "Write-Output ('Size=' + $d.Size); "
                "Write-Output ('FreeSpace=' + $d.FreeSpace)"),
            timeout=20)
        kv = _parse_kv_lines(result.stdout)
        try:
            return {
                "total_gb": round(float(kv.get("Size", 0)) / 1024**3, 1),
                "free_gb": round(float(kv.get("FreeSpace", 0)) / 1024**3, 1),
            }
        except ValueError:
            return {}
    else:  # macOS / Linux usano entrambi df
        path = target.models_dir or "/"
        result = executor.run(
            f'df -k "{path}" 2>/dev/null | tail -1 | '
            'awk \'{print "TOTAL="$2; print "AVAIL="$4}\'', timeout=8)
        kv = _parse_kv_lines(result.stdout)
        try:
            return {
                "total_gb": round(float(kv.get("TOTAL", 0)) * 1024 / 1024**3, 1),
                "free_gb": round(float(kv.get("AVAIL", 0)) * 1024 / 1024**3, 1),
            }
        except ValueError:
            return {}


def detect_hardware(executor: Executor, target: Target) -> dict:
    """Rilevamento hardware completo"""
    info = {}
    info["gpu"] = _detect_gpu_static(executor, target)
    info["cpu"] = _detect_cpu_static(executor, target)
    info["memory"] = _detect_memory_static(executor, target)
    info["disk"] = _detect_disk(executor, target)
    return info


def collect_all(executor: Executor, target: Target) -> dict:
    """Istantanea di monitoraggio"""
    return {
        "gpu": _collect_gpu(executor, target),
        "cpu_mem": _collect_cpu_mem(executor, target),
        "metrics": collect_metrics(executor, target),
    }
