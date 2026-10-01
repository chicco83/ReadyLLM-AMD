# CHANGELOG — ReadyLLM-AMD

Versione corrente: **1.1.16** — 2026-10-01 22:50

Le voci sono numerate in ordine; ogni sessione aggiunge la propria in cima o in coda mantenendo la numerazione.

## 18. v1.1.16 — 2026-10-01 22:50 — Release scelta per l'installazione
- `/releases/latest` puntava a v0.5.0 (1 asset, non binario): ora si usa l'elenco delle release e si sceglie la prima con pacchetti `bin-win`.

## 17. v1.1.15 — 2026-10-01 22:49 — Installazione Windows: asset non trovati
- Elenco release con tag + log della fonte; ripiego sui nomi standard dei pacchetti ricostruiti dal tag; pattern ROCm `win-rocm`; cudart dal tag.

## 16. v1.1.14 — 2026-10-01 22:44 — Attiva build, installazione per backend
- Elenco build installate con pulsante «Attiva» (cambia engine_path automaticamente).
- Pulsante Installa sempre visibile per llama.cpp, con scelta del backend nella riga (prima non compariva se un motore era gia' configurato).

## 15. v1.1.13 — 2026-10-01 22:40 — Diagnosi GPU non usata
- `/api/deploy/log` analizza il log (strati su GPU, dispositivi); il Deploy avvisa quando 0 strati sono offloadati e mostra «GPU in uso: N/M strati».

## 14. v1.1.12 — 2026-10-01 22:39 — Motore nascosto con log, Deploy e Tuning sotto il monitoraggio
- Windows (target locale): llama-server avviato con finestra nascosta; log visibile da «Log del motore» (`GET /api/deploy/log`).
- Il tuning avvisa nel log che ferma e riavvia il server avviato dal Deploy.
- Deploy e Tuning spostati sotto il Monitoraggio, con pulsanti dei passaggi in evidenza (1 ➜ 2); rimossa la voce di menu Deploy.

## 13. v1.1.11 — 2026-10-01 22:29 — Deploy→Tuning, baseline dal Deploy, MTP condizionato
- Pagina Deploy con due schede in sequenza (Deploy, Tuning); tuning tolto da Monitoraggio (etichetta menu ripristinata). Modello condiviso tra le schede.
- Il tuning usa l'elenco di `/api/deploy/models` (sottocartelle incluse; prima i modelli in sottocartelle non si avviavano).
- Baseline = parametri del Deploy (modificabile) invece di quella fissa draft-mtp/q4_0/batch4096; opzioni «predefiniti motore» e «nessuna».
- draft-mtp proposto solo se supportato da modello e build; parametri `spec-*`/`*draft*` rimossi altrimenti (tuning e default del Deploy).

## 12. v1.1.10 — 2026-10-01 22:24 — Backend del motore e selezione percorsi
- Rilevamento del backend del llama-server installato (Vulkan/ROCm/CUDA/CPU) con elenco dispositivi, mostrato in Impostazioni.
- Installazione in cartelle separate per backend.
- Pulsanti «Sfoglia…» con finestra nativa di Windows per percorso del motore e cartella dei modelli (solo target locali).

## 11. v1.1.9 — 2026-10-01 22:21 — Monitoraggio e tuning unificati
- Il tuning intelligente e' una sezione della pagina Monitoraggio; rimosse la voce di menu e la pagina separata. Etichetta menu: «Monitoraggio e tuning» (en/it/zh).

## 10. v1.1.8 — 2026-10-01 22:20 — Tuning su Windows
- Dimensione modello rilevata correttamente (era 0.0 GB).
- llama-server su Windows scrive in `C:\temp\llama_server.log`; le ultime righe appaiono nel log del tuning in caso di errore/timeout.
- Attesa di avvio 120 -> 300 s.

## 9. v1.1.7 — 2026-10-01 22:19 — Versione driver
- VRAM RX 9070 XT verificata (15.8 GiB). Decodifica della versione driver DirectX (uint64) in formato a.b.c.d.

## 8. v1.1.6 — 2026-10-01 22:16 — VRAM AMD su Windows
- `collectors.py`: nuova fonte DirectX (DedicatedVideoMemory, 64 bit) per la VRAM; il valore da `AdapterRAM` (max 4 GB) resta solo come ultimo ripiego.
- `avvia.py --diagnosi-gpu`: stampa risultato e output grezzo delle fonti.

## 7. v1.1.5 — 2026-10-01 22:11 — Frontend da copia locale su Drive
- La giunzione di v1.1.4 falliva ("sono necessari volumi NTFS locali"): `avvia.py` ora copia il frontend con robocopy in `%LOCALAPPDATA%\ReadyLLM-AMD\frontend` ed esegue npm/vite da li'.

## 6. v1.1.4 — 2026-10-01 22:09 — avvia.py e Google Drive
- `avvia.py`: rilevamento di cartelle su Google Drive; `--installa` crea node_modules su disco locale (giunzione) per evitare i TAR_ENTRY_ERROR di npm.

## 5. v1.1.3 — 2026-10-01 22:04 — Script di avvio
- Aggiunto `avvia.py` nella radice (avvio di backend e frontend, opzioni `--installa`, `--comandi`, `--backend`, `--frontend`).

## 4. v1.1.2 — 2026-10-01 22:00 — Flusso git su main
- Il lavoro v1.1.0/v1.1.1 è stato portato su `main` (fast-forward); `main` è il ramo e repository predefinito delle sessioni.

## 3. v1.1.1 — 2026-10-01 21:55 — Installazione llama.cpp più robusta
- Installazione Windows: TLS 1.2 forzato, User-Agent, ripiego HTML se l'API GitHub fallisce (403/limite/blocco),
  3 tentativi di download con controllo dimensione, mirror opzionale `READYLLM_GH_PROXY`, DLL `cudart` per il backend CUDA.
- `_run_step(check=True)` su download/estrazione/clone/compilazione: l'errore reale non è più scartato.
- Aggiunto `UPSTREAM_PROPOSAL.md` (testo pronto per proporre i fix all'autore originale).

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
- Aggiunti `CLAUDE.md`, `CONTEXT_v1.1.1_2026-10-01_2155.md`, `MANUAL_v1.1.1_2026-10-01_2155.md`, questo `CHANGELOG.md`.

## 1. v1.0.0 — baseline (commit c84e8f5)
- Stato del repository prima di questa sessione (SGLang, i18n en/zh, tuning, monitor, store, ComfyUI).
