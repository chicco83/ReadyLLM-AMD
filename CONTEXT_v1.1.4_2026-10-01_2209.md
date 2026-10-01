# CONTEXT — ReadyLLM-AMD

Versione: **1.1.4** — 2026-10-01 22:09

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
`avvia.py --installa` su Google Drive (Windows): node_modules viene creato in `%LOCALAPPDATA%\ReadyLLM-AMD\node_modules` e collegato con giunzione, per evitare gli errori npm TAR_ENTRY_ERROR del disco virtuale. Non verificato su Windows reale.
