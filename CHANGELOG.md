# CHANGELOG — ReadyLLM-AMD

Versione corrente: **1.1.27** — 2026-10-02 03:00

Le voci sono numerate in ordine; ogni sessione aggiunge la propria in cima o in coda mantenendo la numerazione.

## 29. v1.1.27 — 2026-10-02 03:00 — Tuning multi-motore, applica+riavvia, prima/dopo, obiettivo Coding
- **Altri motori:** opzione «Prova anche gli altri motori installati» (visibile con 2+ build GPU, es. Vulkan e ROCm): a fine tuning la configurazione migliore e' rimisurata con le altre build; il motore cambia solo se e' almeno il 3% piu' veloce. Le build non utilizzabili con la configurazione sono saltate.
- **Salva e applica** (`POST /api/tune/apply`): salva i parametri, se consigliato cambia motore (engine_path/backend) e RIAVVIA il modello con gli stessi parametri misurati (flash-attn, fit off compresi).
- Avviso nel Tuning: il tuning prende il controllo di llama-server e lo lascia fermo a fine prove.
- **Grafico prima vs dopo** (decodifica, prefill, TTFT, % e motore) nella barra di progresso finale e nel riquadro della configurazione consigliata.
- Nuovo obiettivo **Coding e agenti** (prefill 50%, decodifica 40%, TTFT 10%), predefinito; descrizioni di tutti gli obiettivi con il caso d'uso. Lo storico mostra il motore della configurazione consigliata.

## 28. v1.1.26 — 2026-10-02 02:10 — Deploy: «misurati t/s» corretto
- Il Deploy mostrava come «misurati X t/s» il punteggio composito del tuning (es. 42.61) invece della decodifica reale. Ora il tuning (salvataggio automatico e «Salva e applica») registra i t/s di decodifica. I valori gia' salvati restano errati finche' non si rifa un tuning per quel modello.

## 27. v1.1.25 — 2026-10-02 01:50 — Tuning: mai consigliare una config peggiore della baseline
- Bug: veniva consigliato il risultato dell'ultima fase (fine) anche se piu' lento della baseline (68.23 -> 65.5 t/s, -4%): la stessa riga misurata tre volte dava 68.2/66.0/65.5 per rumore di misura.
- Ora la raccomandazione e' la migliore misura tra baseline, coarse e fine; una variante sostituisce la baseline solo se la supera di almeno il 3% (margine di rumore), altrimenti resta la tua configurazione con avviso nel log.
- Le combinazioni coarse identiche alla baseline riusano la sua misura; una sola riga marcata «consigliata».

## 26. v1.1.24 — 2026-10-02 01:20 — Storico ottimizzazioni + fix temperatura GPU
- Nuova pagina «Storico ottimizzazioni» (menu laterale): una riga per ogni tuning concluso con data, modello (e dimensione), motore (backend + versione), GPU, decodifica/prefill, guadagno vs baseline, contesto, durata; dettaglio con TTFT, uso GPU, punteggio, prove, parametri consigliati e baseline, percorso motore. Filtri per testo/esito/macchina, eliminazione voce.
- Backend: `services/tune_log.py` (archivio `~/.model-deploy-assistant/tune_log.json`, max 500 voci), registrato da `_finalize`/`_fail`; `GET /api/tune/history`, `DELETE /api/tune/history/{id}`. Il job salva i metadati di motore/GPU/modello.
- Temperatura GPU: la query D3DKMT restituiva 0xC000000D perche' il buffer non era azzerato (PhysicalAdapterIndex e' un ingresso); ora e' inizializzato. Il rumore CLIXML di PowerShell non finisce piu' nel log.

## 25. v1.1.23 — 2026-10-02 00:50 — Esito del tuning, grafico token, temperatura, cache hit
- A fine tuning la barra di progresso resta (verde/rossa) con esito, configurazione consigliata, «Vedi risultati» e chiusura con X; il passaggio scelto (Deploy/Tuning) e' ricordato e Tuning ripristina l'ultimo risultato (`GET /api/tune/last`) invece di tornare vuoto.
- Grafico token per giorno: asse destro dedicato all'output (prima invisibile accanto ai token prompt, molto piu' numerosi).
- Temperatura GPU: script piu' compatto e motivo del fallimento scritto nella console del backend (`Temperatura GPU non disponibile: ...`).
- Cache hit: se la build non usa il nome standard della metrica dei token in cache se ne cerca una equivalente. Con lo scheduling a 4 slot a rotazione del benchmark il riuso del prefisso e' realmente 0.

## 24. v1.1.22 — 2026-10-02 00:20 — Motore nella barra di progresso
- Al centro della barra di progresso del tuning compaiono backend e versione del motore (es. «ROCM — version: 0.5.0-dev (build 11327, commit 552f18f91)»).

## 23. v1.1.21 — 2026-10-02 00:10 — Temperatura GPU su Windows
- Il Monitoraggio legge la temperatura dalla stessa sorgente di Gestione attivita' (`D3DKMTQueryAdapterInfo`, ADAPTERPERFDATA) via PowerShell; cache 5 s, se non supportata dal driver resta «--» (non si riprova per 10 min). Da verificare sul PC reale.

## 22. v1.1.20 — 2026-10-01 23:50 — Tuning: motore che si fermava + barra di progresso
- Bug: il tuner passava `--spec-type off` (valore non valido per llama-server) e il motore usciva dopo la baseline; ora con spec-type=off il parametro e' omesso.
- Barra di progresso live (fase, prove completate/totale stimato, ultime righe del log) nel Monitoraggio, visibile anche con il passaggio Deploy aperto (`GET /api/tune/active` ora include `progress`).

## 21. v1.1.19 — 2026-10-01 23:30 — Pannello «Motori di inferenza»: tutte le build
- Il pannello in basso elenca ogni build di llama-server installata (ROCm e Vulkan) con backend, versione (`--version`), dispositivi e marcatore «In uso».
- Impostazioni: all'apertura si seleziona la macchina salvata (prima il modulo partiva «nuovo» senza id e non mostrava lo stato dei backend).

## 20. v1.1.18 — 2026-10-01 23:00 — Tuning: stima VRAM calibrata, utilizzo GPU, layout Deploy
- Stima VRAM calibrata sul log reale (`llama_kv_cache: size`); niente piu' ripiego automatico su CPU: se la stima scarta tutto si provano le combinazioni q4_0 in GPU.
- Utilizzo GPU su Windows da contatori raw (prima 0%).
- Deploy: log del motore a destra; dopo «Avvia» passaggio automatico al Tuning.

## 19. v1.1.17 — 2026-10-01 22:55 — Backend, attivazione e installazione unificati
- I pulsanti del backend mostrano lo stato (installato / non installato), mettono in uso la build al click e propongono l'installazione con log; rimossa la sezione duplicata sotto «Motori di inferenza».

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
