# CONTEXT — ReadyLLM-AMD

Versione: **1.1.8** — 2026-10-01 22:20

## Scopo
Assistente di deploy/tuning/monitoraggio per LLM locali (llama.cpp, vLLM, SGLang) e generazione video
(ComfyUI), con interfaccia web. Backend FastAPI + frontend React/Vite. Gira su Windows, Linux, macOS; la
macchina «target» può essere locale o remota via SSH.

## Architettura
- `backend/app/models/target.py`: dataclass `Target` + persistenza in `~/.model-deploy-assistant/targets.json`
  (`upsert_target`, `get_target`, `delete_target`). Campo `llama_backend` (auto|cuda|rocm|vulkan|cpu).
- `backend/app/services/executor.py`: esecuzione comandi locale/SSH (`ssh`/`scp` di sistema).
- `backend/app/services/collectors.py`: hardware statico (`detect_hardware`) e monitor realtime (`collect_all`);
  GPU vendor-agnostiche (NVIDIA: nvidia-smi; AMD/altri: registro/WMI su Windows, sysfs su Linux).
- `backend/app/services/model_scanner.py`: scansione ricorsiva dei `.gguf`.
- `backend/app/services/installer.py`: rilevamento e installazione motori; `resolve_llama_backend`.
- `backend/app/services/{llama_cpp,vllm,sglang,comfyui}.py`: adattatori motore; `engine_registry.py`: registro.
- `backend/app/services/{tuner,ai_tuner,config_generator,tune_history}.py`: tuning.
- `backend/app/services/video_*.py`, `upscale_pipeline.py`, `h3_prompt_format.py`: generazione video (ComfyUI).
- `frontend/src/pages/*`: Monitor, Store, Deploy, Tune, Settings, LongVideoDeploy; i18n in `src/i18n` (en/it/zh).

## Decisioni di questa revisione (1.1.0)
1. **Bug targets.json**: causa = form senza `id`. Doppia difesa frontend (selettore/edit) + backend (match per identità).
2. **GPU AMD non rilevata**: causa = solo `nvidia-smi`. Su Windows `Win32_VideoController.AdapterRAM` è uint32
   (max 4 GB): la VRAM si legge dal registro `qwMemorySize`. Tra più GPU (iGPU+dGPU) si sceglie quella con più VRAM.
3. **ROCm vs Vulkan** (valutazione, implementata come scelta utente):
   - Vulkan: nessun SDK, basta il driver; funziona su RDNA2/3/4 (RX 9070 XT = gfx1201); token generation spesso
     alla pari con ROCm; prompt processing generalmente più lento. Default sicuro per AMD.
   - ROCm/HIP: tipicamente più veloce nel prefill; Windows: pacchetto precompilato `hip-radeon` (include le DLL HIP,
     serve solo il driver Adrenalin recente); Linux: serve ROCm ≥ 6.4 installato (gfx1201 non supportato prima) e
     compilazione da sorgente (`GGML_HIP=ON`, `AMDGPU_TARGETS=gfx1201`).
   - Scelta: `Target.llama_backend` (UI: Impostazioni → «Backend di llama.cpp»); `auto` = NVIDIA→CUDA, AMD→Vulkan.
   - Non verificato su hardware reale in questa sessione (ambiente cloud senza GPU): i nomi degli asset delle release
     llama.cpp vengono confrontati per sottostringa (`_WIN_ASSET_PATTERNS`) e, se nessuno combacia, l'errore elenca gli asset disponibili.
4. **Lingua**: tutto il cinese non-i18n tradotto in italiano; nuova lingua UI `it` (default).

## Limiti noti / da fare
- Temperatura e potenza GPU non sono esposte da Windows per le schede AMD (restano 0 nel Monitor).
- La VRAM libera su Windows non è disponibile in rilevamento statico (`free_memory_gb` = 0).
- Test hardware AMD reali da eseguire sul PC con RX 9070 XT (vedi MANUAL, sezione «Verifica GPU AMD»).
- Le raccomandazioni modelli (`api/hardware.py`) si basano solo sulla VRAM totale.

## Aggiornamento 1.1.1 — installazione llama.cpp (errore di rete)
Cause probabili dell'errore di rete nell'installazione con un clic su Windows: PowerShell 5.1 senza TLS 1.2 forzato,
API `api.github.com` bloccata o con limite 60 richieste/ora (403), errore reale scartato. Correzioni in `installer.py`:
TLS 1.2 + User-Agent, ripiego sulla pagina HTML `releases/expanded_assets/<tag>`, 3 tentativi con verifica dimensione,
mirror opzionale `READYLLM_GH_PROXY`, `_run_step(check=True)` che propaga l'errore reale, DLL `cudart` per CUDA.
Proposta all'autore originale: vedere `UPSTREAM_PROPOSAL.md`.

## Aggiornamento 1.1.2 — flusso git
Il ramo di lavoro e repository predefinito è `main` (origin: https://github.com/chicco83/ReadyLLM-AMD): le sessioni fanno commit e push direttamente su `main`.

## Aggiornamento 1.1.3
Aggiunto `avvia.py` nella radice: avvia backend (uvicorn, porta 8000) e frontend (Vite, porta 3000) con un solo comando.

## Aggiornamento 1.1.4
`avvia.py --installa` su Google Drive (Windows): (v1.1.4, SOSTITUITO: la giunzione mklink /J fallisce perche' Drive non e' NTFS).

## Aggiornamento 1.1.5
Su Drive (Windows) `avvia.py` esegue il frontend da una copia locale in `%LOCALAPPDATA%\ReadyLLM-AMD\frontend`, rinnovata a ogni avvio. Non verificato su Windows reale.

## Aggiornamento 1.1.6 — VRAM AMD su Windows
VRAM rilevata a 4 GB = ripiego su `Win32_VideoController.AdapterRAM` (uint32). Aggiunta la fonte `HKLM\SOFTWARE\Microsoft\DirectX\<guid>\DedicatedVideoMemory` (64 bit) e `avvia.py --diagnosi-gpu`. Non verificato sulla RX 9070 XT reale.

## Aggiornamento 1.1.7
Verificato sulla RX 9070 XT reale: VRAM 15.8 GiB da `DedicatedVideoMemory` (DirectX). La chiave di classe display non espone qwMemorySize su questo PC. Versione driver DirectX decodificata da uint64 (es. 32.0.31036.15).

## Aggiornamento 1.1.8 — tuning su Windows
Test di tuning fallito: VRAM 4.0 GB (backend avviato prima del fix, senza riavvio: uvicorn gira senza --reload), dimensione modello 0.0 GB (comando PowerShell fragile) e timeout di avvio senza diagnosi (output di llama-server perso su Windows). Correzioni: dimensione modello robusta, log in `C:\temp\llama_server.log` con ultime righe nel log del tuning, attesa avvio 300 s.
