# CHANGELOG — ReadyLLM-AMD

Versione corrente: **1.1.0** — 2026-10-01 21:50

Le voci sono numerate in ordine; ogni sessione aggiunge la propria in cima o in coda mantenendo la numerazione.

## 2. v1.1.0 — 2026-10-01 21:50 — Fix target.json, scansione ricorsiva, supporto AMD, scelta backend, italiano

### Correzioni
- **Bug «nuova entry invece di aggiornare» in `targets.json`**: il form Impostazioni partiva sempre vuoto e
  inviava il salvataggio senza `id`; `Target()` generava un nuovo uuid e `upsert_target` accodava la entry.
  - Frontend (`Settings.jsx`): selettore delle macchine salvate (modifica / «Nuova»); il form conserva l'`id`
    anche dopo il salvataggio.
  - Backend (`models/target.py`, `api/target.py`): `upsert_target(match_identity=True)` riconosce la stessa
    macchina (ssh: host+porta+utente; locale: nome) anche senza `id` e la aggiorna riusando l'`id` esistente.
- **Radeon RX 9070 XT non rilevata**: il rilevamento GPU provava solo `nvidia-smi` (e `system_profiler` su macOS).
  Aggiunti (`collectors.py`): Windows → registro di sistema (`HardwareInformation.qwMemorySize`, perché
  `AdapterRAM` satura a 4 GB) con ripiego su `Win32_VideoController`; Linux → sysfs `amdgpu` + `lspci` + `rocminfo`
  (architettura gfx, es. gfx1201). Monitor realtime: Linux via sysfs, Windows via classi WMI
  `Win32_PerfFormattedData_GPUPerformanceCounters_*` (nomi non localizzati, funzionano su Windows in italiano).
  Il dizionario hardware ora contiene anche `vendor` (nvidia/amd/intel/apple) ed eventualmente `gfx`.

### Nuove funzioni
- **Scansione ricorsiva dei modelli** (`services/model_scanner.py`, usata da `/api/deploy/models`): trova i `.gguf`
  in tutte le sottocartelle; restituisce percorsi relativi a `models_dir`; esclude shard successivi al primo
  (`-0000N-of-0000M`, N>1) e i file `mmproj*`. Anche `/api/store/downloaded` ora è ricorsivo.
- **Scelta del backend llama.cpp** (CUDA / ROCm-HIP / Vulkan / CPU / Auto): nuovo campo `Target.llama_backend`,
  selettore in Impostazioni, `installer.resolve_llama_backend`. Windows: scelta del pacchetto precompilato
  (`cuda`, `hip-radeon`, `vulkan`, `cpu`); Linux: flag cmake `GGML_CUDA` / `GGML_HIP` (+`AMDGPU_TARGETS`) /
  `GGML_VULKAN`. Auto: NVIDIA→CUDA, AMD/Intel→Vulkan, Apple→Metal (brew), altro→CPU.
- **Lingua italiana** nell'interfaccia (en / it / zh, default `it`); `docs/benchmark-it.svg`.

### Traduzione
- Tutti i commenti, docstring, messaggi API, log e testi dei prompt LLM che erano in cinese sono stati
  tradotti in italiano (backend e frontend). La logica è invariata (verificata confrontando l'AST prima/dopo,
  con le stringhe mascherate). Restano in cinese solo la localizzazione `zh` e `README.zh-CN.md`.
- Prompt di benchmark (`tuner.py`) e prompt di sistema (`video_*`, `ai_tuner`) ora in italiano: le misure di
  velocità dei nuovi tuning possono differire leggermente da quelle storiche (tokenizzazione diversa).

### Documentazione
- Aggiunti `CLAUDE.md`, `CONTEXT_v1.1.0_2026-10-01_2150.md`, `MANUAL_v1.1.0_2026-10-01_2150.md`, questo `CHANGELOG.md`.

## 1. v1.0.0 — baseline (commit c84e8f5)
- Stato del repository prima di questa sessione (SGLang, i18n en/zh, tuning, monitor, store, ComfyUI).
