# CONTEXT — ReadyLLM-AMD

Versione: **1.1.18** — 2026-10-01 23:00

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

## Aggiornamento 1.1.9 — UI
Il tuning intelligente (automatico + AI) e' una sezione della pagina Monitoraggio (`Monitor.jsx` include `<Tune embedded />`); la voce di menu «Tuning intelligente» e la route `tune` sono state rimosse.

## Aggiornamento 1.1.10 — motore e selezione percorsi
- `installer.detect_llama_backends`: backend del llama-server installato da (1) `ggml-*.dll`/`libggml-*.so` accanto all'eseguibile e (2) `llama-server --list-devices` (dispositivi effettivi, indizio prioritario). Esposto in `/api/target/<id>/engine` come `backend`, `backends`, `devices`.
- Installazione per backend in cartelle separate (`C:\llama\<backend>` su Windows, `/tmp/llama.cpp-<backend>` su Linux).
- `POST /api/target/pick`: finestra nativa Tkinter (file/cartella) aperta dal backend; valido solo per target locali. Non verificato su Windows reale.

## Aggiornamento 1.1.11 — Deploy → Tuning, baseline, MTP
- UI: la pagina Deploy (motori testuali llama.cpp) ha due schede in sequenza «1 · Deploy» e «2 · Tuning»; il tuning e' stato tolto da Monitoraggio (v1.1.9). Modello condiviso via `frontend/src/lib/lastModel.js` (localStorage per target). Elenco modelli del tuning da `/api/deploy/models` (percorsi relativi, anche sottocartelle).
- Baseline del tuning = parametri del Deploy per il modello (`GET /api/tune/baseline`: ultimo tuning o generatore), modificabile; modalita' deploy / predefiniti motore / nessuna. `POST /api/tune/start` accetta `baseline_args` (stringa); `start_tune`: `None`=nessuna baseline, `{}`=predefiniti motore.
- MTP: `tuner.mtp_state` = modello (nome file) AND build (`llama-server --help` cita "mtp"). Se non supportato: draft-mtp non proposto nella fase coarse, parametri `spec-*`/`*draft*` tolti dalla baseline e da `/api/deploy/default-args`.
- Limite noto: `_estimate_vram_gb` usa dimensioni KV tarate su un 27B (8192 x 64 strati): per modelli piu' piccoli e ctx molto alti sovrastima e scarta tutte le combinazioni GPU. Non ancora corretto.

## Aggiornamento 1.1.12 — avvio motore su Windows, UI
- Avvio llama-server (target locale Windows): `Start-Process -WindowStyle Hidden` invece di schtasks (resta schtasks per SSH). Causa della «shell nera»: con v1.1.8 l'output e' rediretto su `C:\temp\llama_server.log`, quindi la finestra visibile restava vuota. Nuovo `GET /api/deploy/log` e pulsante «Log del motore» nel Deploy.
- «Si avvia due volte»: il tuning ferma il server del Deploy e lo riavvia a ogni prova (comportamento voluto); ora lo dichiara nel log del tuning.
- UI: Deploy e Tuning sono sotto il Monitoraggio (`<Deploy embedded />` in `Monitor.jsx`), con due pulsanti a passaggi evidenziati «1 Deploy ➜ 2 Tuning»; voce di menu «Deploy» rimossa; menu «Monitoraggio e deploy».

## Aggiornamento 1.1.13 — GPU non usata
Segnalazione: nel monitoraggio GPU 0%, VRAM 3 GB/15.8, CPU 71%, 7.7 t/s con un 9B Q8_0 = modello su CPU. Causa probabile: llama-server CPU/CUDA (installato prima di v1.1.0, quando l'installer scaricava sempre CUDA) invece di Vulkan/ROCm. `GET /api/deploy/log` ora restituisce `offload` ("offloaded N/M layers to GPU") e `gpu_devices`; la pagina Deploy mostra un avviso se N=0. Non verificato sul PC reale.

## Aggiornamento 1.1.14 — Attiva build e installazione per backend
- Causa del «non scarica niente»: con un motore gia' configurato (engine_path) il pannello risultava «Installato» e il pulsante Installa non compariva; inoltre il backend scelto nel form valeva solo dopo «Salva». Ora la riga di ogni macchina llama.cpp ha sempre «Installa una nuova build con backend: [select] [Installa]» (il backend viaggia in `POST /api/target/install-engine` come `backend`).
- `GET /api/target/<id>/engines-installed` (`installer.find_llama_installs`: `C:\llama\*`, `/tmp/llama.cpp-*`, PATH, motore corrente, con backend rilevato) e `POST /api/target/<id>/activate-engine` (imposta `engine_path`): pulsante **Attiva**.

## Aggiornamento 1.1.15 — installazione Windows: asset non trovati
Segnalazione: «Nessun pacchetto Windows per il backend cpu... Asset Windows disponibili: nessuno» per tutti i backend, benche' la release b11327 abbia i pacchetti (`llama-<tag>-bin-win-{cpu,vulkan,cuda-12.4,cuda-13.4,rocm-10.0}-x64.zip`, `cudart-llama-bin-win-cuda-12.4-x64.zip`). La causa dell'elenco vuoto sul PC non e' stata riprodotta (nel cloud GitHub e' bloccato). Correzioni: `_win_release_urls` restituisce (urls, tag) e logga fonte e conteggio; ripiego HTML piu' tollerante (href relativi/assoluti, anche .tar.gz); se l'elenco manca ma il tag e' noto, `_candidate_win_urls` ricostruisce gli URL dai nomi standard e li prova in ordine; pattern ROCm aggiornato a `bin-win-rocm` (prima `hip-radeon`); cudart ricostruito dal tag. Da verificare sul PC reale: il log ora dice quale fonte ha risposto.

## Aggiornamento 1.1.16 — release «latest» sbagliata
Dal log dell'utente: `/releases/latest` di ggml-org/llama.cpp restituisce il tag `v0.5.0` con 1 solo asset (non binario), mentre i pacchetti sono nelle release `bNNNNN` (es. b11327). Ora `_win_release_urls` legge `releases?per_page=20` e sceglie la prima release (dalla piu' recente) con almeno un asset `bin-win`; ripiego sulla pagina HTML delle release (primo tag `bNNNN`, expanded_assets) e sui nomi standard. Anche l'estrazione cudart parte dal tag corretto.

## Aggiornamento 1.1.17 — UI coerente per backend/installazione
Rimossa da `EngineRow` la sezione «Build installate / Attiva / Installa»; spostata nel form sotto i pulsanti del backend (`pickBackend`, `installBackend`, `buildFor` in `Settings.jsx`). `POST /api/target/<id>/activate-engine` accetta `path` opzionale e `llama_backend`.

## Aggiornamento 1.1.18 — tuning, GPU util, layout Deploy
- Tuning dell'utente (9B Q8_0, ctx 262144, 15.8 GB): baseline in GPU 47.8 t/s, ma la stima VRAM (kv_dim 8192, 64 strati) scartava TUTTE le combinazioni GPU e il tuning ripiegava su CPU. Ora: calibrazione sulla riga reale `llama_kv_cache: size = X MiB` del log (salvata in `_JOBS[job]["kv_calib"]`), stima senza calibrazione con kv_dim 1024 e strati dedotti dalla dimensione, margine 5%; se tutto viene scartato si provano comunque le combinazioni q4_0 in GPU (niente piu' ripiego automatico su CPU).
- Utilizzo GPU Windows: contatori `Win32_PerfRawData_GPUPerformanceCounters_GPUEngine` con due campioni a 0.7 s (le classi Formatted davano 0%). Non verificato su PC reale.
- Deploy: comandi a sinistra e «Log del motore» a destra (come l'avanzamento del tuning); dopo «Avvia» si passa da solo al passo 2 (Tuning).

- v1.1.19: `find_llama_installs` restituisce anche `version`; `EngineRow` mostra tutte le build da `GET /api/target/<id>/engines-installed`; Settings preseleziona `targets[0]`.

- v1.1.20: `_args_list` omette `spec-type` se off/none; `tuner._set_progress` + campo `progress` nei job; componente `TuneLiveProgress` in Monitor.

- v1.1.21: `collectors._gpu_temp_windows` (script C# P/Invoke gdi32 D3DKMTQueryAdapterInfo tipo 62, LUID dai contatori GPUAdapterMemory, -EncodedCommand, cache).

- v1.1.22: `TuneLiveProgress` legge `GET /api/target/<id>/engine` (backend + version) e lo mostra nella barra.

- v1.1.23: `tuner.get_last_job` + `GET /api/tune/last`; `TuneLiveProgress` con stato finale; Deploy step in sessionStorage + evento `readyllm:goto-tune`; grafico token con 2 assi Y.

- v1.1.24: `services/tune_log.py` (lista di voci per tuning), job con `ts_start`+`meta` (engine/gpu/model_size), pagina `History.jsx`, voce di menu `history`; fix temperatura: buffer P azzerato con StructureToPtr.

- v1.1.25: tuner worker sceglie `max(score)` su all_results con soglia 3% sulla baseline; `_coarse_search(baseline_result=...)` riusa la misura se la config e' identica; `_finalize` marca `recommended` per identita'.

- v1.1.26: `tune_history.score` ora = decodifica t/s (tuner._finalize e Tune.saveBest); il punteggio composito resta solo in tune_log/risultati.

- v1.1.27: `tuner._alt_engines/_try_other_engines` (dataclasses.replace(target, engine_path=...)); `try_engines` in TuneRequest; `POST /api/tune/apply`; `GOAL_WEIGHTS['coding']`; componente `BeforeAfter`; i risultati portano il campo `engine`.

- v1.1.28: `_goal_extras` (parallel/cache-reuse per coding), `_BENCH_XLONG_PROMPT` + metrica `prefill_long`, `_score` usa prefill_long per coding, sonda flash-attn in `_fine_search`, `NOISE_MARGIN` costante, ubatch grid con 64 (indice 256 = [2]).

- v1.1.29: `TuningImpact` (Monitor), `SpecCard` + `deploy._record_args/GET /api/deploy/spec`, `tuner._offload_from_log` e controllo `devices` in `_try_other_engines`.
