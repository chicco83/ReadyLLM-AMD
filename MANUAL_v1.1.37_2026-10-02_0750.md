# MANUALE — ReadyLLM-AMD

Versione: **1.1.18** — 2026-10-01 23:00

## 1. Avvio
Dalla radice del progetto: `python avvia.py` (backend + frontend), `python avvia.py --installa` (installa prima le dipendenze), `python avvia.py --comandi` (stampa i comandi manuali), `--backend` / `--frontend` per avviarne uno solo. Ctrl+C li ferma entrambi. Comandi manuali:
```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
# Frontend
cd frontend && npm install && npm run dev      # http://localhost:3000
```

## 2. Impostazioni → macchine target
- Le macchine salvate compaiono come pulsanti in alto: cliccare per **modificare**; «+ Nuova» per aggiungerne una.
- «Salva» aggiorna la entry selezionata (non ne crea più una nuova). La configurazione è in
  `~/.model-deploy-assistant/targets.json` (la password non viene salvata).
- **Backend di llama.cpp**: Auto / CUDA / ROCm (HIP) / Vulkan / CPU. Si applica all'installazione con un clic.

## 3. Cartella dei modelli
La ricerca dei `.gguf` è **ricorsiva** (fino a 8 livelli): `D:\models\qwen\Qwen3-8B-Q4_K_M.gguf` compare come
`qwen\Qwen3-8B-Q4_K_M.gguf`. Gli shard successivi al primo (`…-00002-of-00005.gguf`) e i file `mmproj*` sono nascosti.

## 4. GPU AMD (es. Radeon RX 9070 XT)
- Rilevamento: Windows (registro di sistema), Linux (sysfs amdgpu + lspci; `rocminfo` per l'architettura gfx).
- **Verifica GPU AMD** sul PC: Impostazioni → «Verifica la connessione» deve mostrare `GPU: AMD Radeon RX 9070 XT (16.0G)`.
  Se la GPU manca, eseguire in PowerShell e inviare l'output:
  `Get-ItemProperty 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11cd-bfc1-08002be10318}\0*' | Select DriverDesc, 'HardwareInformation.qwMemorySize'`
- Monitor: Windows mostra utilizzo e VRAM (temperatura/potenza non disponibili); Linux mostra anche temperatura e potenza.

## 5. Scegliere ROCm o Vulkan
| Backend | Quando | Requisiti |
|---|---|---|
| Vulkan | Default per AMD, «funziona subito» | Driver grafico aggiornato (Adrenalin / Mesa RADV); Linux compilazione: `libvulkan-dev` + `glslc` |
| ROCm (HIP) | Massimo prefill su RDNA | Windows: driver Adrenalin recente (pacchetto `hip-radeon`); Linux: ROCm ≥ 6.4 + `hipconfig` |
| CUDA | Schede NVIDIA | Driver NVIDIA; Linux compilazione: CUDA Toolkit (`nvcc`) |
| CPU | Nessuna GPU | — |
Provare entrambi con lo stesso modello (tuning automatico) e confrontare decodifica/prefill: per la RX 9070 XT
(gfx1201) Vulkan è il punto di partenza consigliato, ROCm da provare se è installato.

## 6. Lingua
Il pulsante in basso nella barra laterale ruota English → Italiano → 中文. Predefinita: Italiano.

## 7. Risoluzione problemi
- *GPU non rilevata*: vedere sezione 4; su Linux verificare che `/sys/class/drm/card*/device/vendor` valga `0x1002`.
- *Errore «Nessun pacchetto Windows per il backend …»*: l'ultima release di llama.cpp non ha l'asset richiesto o GitHub non è raggiungibile; l'errore elenca gli asset disponibili.
- *Backend ROCm su Linux*: servono `hipconfig` nel PATH e ROCm ≥ 6.4.

## 8. Installazione di llama.cpp: problemi di rete
- L'errore reale ora compare nel log di installazione. Se GitHub non è raggiungibile dalla macchina target:
  1. controllare rete, proxy/firewall e data/ora di sistema;
  2. impostare la variabile d'ambiente `READYLLM_GH_PROXY` (es. `https://ghfast.top/`) prima di avviare il backend;
  3. oppure scaricare a mano il pacchetto da https://github.com/ggml-org/llama.cpp/releases (Vulkan: `bin-win-vulkan-x64`,
     ROCm: `bin-win-hip-radeon-x64`), decomprimerlo e indicare il percorso di `llama-server.exe` in Impostazioni.

## 9. Errori npm TAR_ENTRY_ERROR su Google Drive
Causa: Drive per desktop e' un disco virtuale. Soluzioni: (1) spostare il progetto in `C:\dev` (consigliato); (2) `python avvia.py --installa` copia il frontend in `%LOCALAPPDATA%\ReadyLLM-AMD\frontend` (robocopy) ed esegue npm/vite da li'; (3) sospendere Drive durante `npm ci`.

## 10. Diagnosi GPU/VRAM
Se la VRAM e' errata esegui `python avvia.py --diagnosi-gpu` e invia l'output (mostra le fonti CLASS / DX / VC con i relativi valori).

## 11. Il tuning non parte / timeout di avvio
1. Riavvia SEMPRE il backend dopo un `git pull` (Ctrl+C e `python avvia.py`). 2. Nel log del tuning compaiono ora le righe `[llama-server]` con la causa reale (parametro non supportato, VRAM, DLL mancanti). 3. Il log completo e' in `C:\temp\llama_server.log`.

## 12. Monitoraggio e tuning in una sola pagina
(v1.1.11: sostituito) Il tuning non e' piu' nella pagina Monitoraggio: vedi sezione 14.

## 13. Percorsi e backend del motore
- In Impostazioni, accanto a «Percorso del motore» e «Cartella dei modelli» c'e' il pulsante **Sfoglia…** (solo target locale): apre la finestra standard di Windows.
- Nel pannello «Motori di inferenza» compare il **backend rilevato** (VULKAN / ROCM / CUDA / CPU) con i dispositivi.
- Puoi avere piu' installazioni (es. `C:\llama\vulkan` e `C:\llama\rocm`) e scegliere quale usare con «Sfoglia…» sul percorso del motore.

## 14. Deploy e Tuning in sequenza
- Pagina **Deploy** → scheda **1 · Deploy** (scegli modello, parametri, Avvia) e scheda **2 · Tuning** (stesso modello gia' selezionato).
- Nel Tuning automatico la **baseline** e' la riga di parametri del Deploy (modificabile); puoi scegliere «Predefiniti del motore» o «Nessuna baseline». Se il modello o la build non supportano MTP, draft-mtp non viene proposto.
- Il tuning ferma/riavvia llama-server: un modello avviato dal Deploy verra' fermato. «Salva e applica» scrive i parametri migliori nel Deploy.

## 15. Motore senza finestra, log e passaggi in sequenza
- Il motore parte senza finestra visibile (target locale). Per vedere cosa fa: pagina Monitoraggio → sezione Deploy → «Log del motore» (si aggiorna ogni 3 s). File: `C:\temp\llama_server.log`.
- Sotto il monitoraggio ci sono i due passaggi **1 Deploy ➜ 2 Tuning**: prima avvia il modello, poi lancia il tuning (che ferma e riavvia il motore piu' volte: e' normale).

## 16. La GPU non viene usata
Sintomi: nel Monitoraggio GPU 0% e VRAM bassa, CPU alta, pochi token/s. Controlli: (1) Impostazioni → «Motori di inferenza» → **Backend rilevato** deve essere VULKAN (o ROCM) per la Radeon; se e' CPU/CUDA, installa llama.cpp con backend Vulkan (Impostazioni → Backend di llama.cpp: Vulkan → Installa) e punta il percorso del motore a `C:\llama\vulkan\llama-server.exe` con «Sfoglia…». (2) Nel Deploy, «Log del motore»: cerca `offloaded N/M layers to GPU`; N deve essere uguale a M. (3) Il Deploy mostra un avviso giallo se 0 strati sono su GPU.

## 17. Backend, build installate e installazione (v1.1.17: unificati)
I pulsanti **Backend di llama.cpp** (Auto / CUDA / ROCm / Vulkan / CPU) del modulo sono il solo comando: ogni pulsante indica se la build e' installata; cliccarlo la mette in uso (cambia da solo il percorso del motore e salva); se non e' installata compare **Installa** con i log. A fine installazione la build diventa quella in uso. Serve una macchina gia' salvata. Il pannello «Motori di inferenza» in basso mostra solo stato, versione e backend rilevato.

## 18. Installazione: «Nessun pacchetto Windows» 
Nel log di installazione ora compare «Release bNNNN: N asset (fonte: API GitHub / pagina HTML)» oppure «uso il tag ... e i nomi standard». Se il download fallisce con 404/errore di rete, il log elenca ogni URL provato. Con `READYLLM_GH_PROXY` si puo' usare un mirror di GitHub.

## 19. Installazione: release trovata
Il log mostra «Release bNNNNN: N asset (fonte: ...)»: se vedi un tag diverso da bNNNNN (es. v0.5.0) segnalalo. Il tag deve iniziare per `b`.

## 20. Log a destra e passaggio automatico
Nel Deploy il log del motore e' sempre visibile a destra. Dopo «Avvia» (a motore avviato) si passa automaticamente alla scheda Tuning. Nel tuning, la riga «KV cache reale: X GB» indica che le stime di VRAM sono state calibrate sul log del motore.

## 18. Elenco build installate (v1.1.19)
Il pannello «Motori di inferenza» in basso elenca tutte le build trovate (es. `C:\llama\rocm` e `C:\llama\vulkan`) con backend, versione e dispositivi; la build in uso e' marcata «In uso». Per cambiarla si usano i pulsanti del backend nel modulo. Alla riapertura delle Impostazioni la macchina salvata e' gia' selezionata.

## 19. Barra di progresso del tuning (v1.1.20)
Durante il tuning, sotto i grafici del Monitoraggio compare una barra con la fase (baseline, coarse, fine), le prove completate sul totale stimato (il totale cresce se la fase fine ne richiede di piu') e le ultime righe del log, nascondibili con «Nascondi log».

## 20. Temperatura GPU (v1.1.21)
Su Windows la temperatura nel Monitoraggio e' letta come in Gestione attivita'. Se mostra «--» il driver non espone il dato (o la lettura e' fallita).

## 21. Motore nella barra (v1.1.22)
La barra di progresso del tuning riporta al centro backend e versione del motore in uso.

## 22. Fine tuning e grafici (v1.1.23)
A fine tuning la barra resta visibile con l'esito; «Vedi risultati» apre il passaggio Tuning con risultati e consigliato. Nel grafico dei token giornalieri l'output ha l'asse destro. Se la temperatura GPU resta «--», controllare la console del backend (riga «Temperatura GPU non disponibile»).

## 23. Storico delle ottimizzazioni (v1.1.24)
Voce di menu «Storico ottimizzazioni»: elenca tutti i tuning conclusi (anche falliti), dal piu' recente. Colonne: data, modello, motore (backend + versione), GPU, decodifica, prefill, variazione rispetto alla baseline, contesto, durata; la decodifica migliore e' in verde. Clic su una riga per i dettagli (parametri consigliati da incollare nel Deploy, baseline, TTFT, uso GPU). Si puo' filtrare e cancellare una voce con il cestino. Lo storico parte dai tuning eseguiti dopo questo aggiornamento. Dati in `~/.model-deploy-assistant/tune_log.json`.

## 24. Come sceglie il tuning (v1.1.25)
La configurazione consigliata e' la migliore per punteggio tra baseline e prove; una variante prevale sulla tua configurazione solo se e' migliore di almeno il 3% (le misure hanno rumore di qualche punto percentuale). Se nessuna variante vince, resta consigliata la configurazione attuale. La tabella «Tutti i record dei test» e' lo storico della singola esecuzione; lo «Storico ottimizzazioni» nel menu raccoglie tutte le esecuzioni.

## 25. Valore «misurati» nel Deploy (v1.1.26)
Il numero accanto a «Precompilato dal tuning automatico» e' la velocita' di decodifica in t/s della configurazione consigliata (prima era il punteggio composito). Per aggiornare un valore vecchio basta rieseguire il tuning del modello.

## 26. Obiettivi di ottimizzazione (v1.1.27)
- **Coding e agenti** (consigliato per Claude Code, Cline, Continue…): contesti lunghi riletti a ogni richiesta + codice generato (prefill 50%, decodifica 40%, TTFT 10%).
- **Percezione end-to-end**: chat e assistenti (equilibrio tra attesa iniziale e velocita' di risposta).
- **Throughput di decodifica**: generazioni lunghe (scrittura, riassunti, batch).
- **Prefill di testi lunghi**: documenti/RAG molto lunghi con poco output.

## 27. Tuning multi-motore e applicazione (v1.1.27)
Con piu' build GPU installate (Impostazioni → Motori di inferenza) compare «Prova anche gli altri motori installati»: la configurazione migliore viene rimisurata con ogni altra build (es. ROCm contro Vulkan) e il motore cambia solo se vince di almeno il 3%. «Salva e applica» salva, cambia motore se serve e riavvia il modello con la configurazione consigliata. Il grafico «Prima/Dopo» confronta decodifica, prefill e TTFT con la variazione percentuale.

## 28. Cosa misura il tuning (v1.1.28)
- Obiettivo **Coding e agenti**: ogni prova usa `--parallel 1 --cache-reuse 256`; il prefill e' misurato anche su ~16k token (serve un contesto di almeno 20000; sotto, resta solo la misura da ~2400). Il tempo di ogni prova cresce di qualche secondo.
- Fase fine: oltre a batch/ubatch/threads/draft si prova `flash-attn` acceso/spento (solo con cache f16; con cache quantizzata la flash attention e' obbligatoria).
- Limite noto: il benchmark usa richieste indipendenti, quindi il beneficio reale di `--cache-reuse` (stesso prefisso a ogni richiesta dell'agente) non e' misurato ma e' applicato.

## 29. Monitoraggio: effetto del tuning e decodifica speculativa (v1.1.29)
- **Effetto del tuning:** sotto i grafici, prima/dopo dell'ultimo tuning concluso e tabella degli ultimi sei.
- **Decodifica speculativa:** scheda sopra i grafici; mostra se il modello usa MTP/ngram (letto dai parametri dell'ultimo avvio dal Deploy: se il modello e' stato avviato altrove risulta «sconosciuta»). MTP sta nella memoria del modello (VRAM con n-gpu-layers all); ngram usa la cronologia dei token (CPU/RAM). Nessuno dei due usa il disco/SSD.
- **ROCm su CPU:** se il confronto tra motori dice «saltato: --list-devices non elenca nessuna GPU», la build ROCm non vede la scheda (driver AMD con HIP non installato o GPU non supportata dalla build). Verifica a mano con `C:\llama\rocm\llama-server.exe --list-devices`.

## 30. ROCm su Windows (v1.1.30)
Impostazioni → Backend: ROCm. Se la build installata non vede la GPU compare l'avviso rosso con **Reinstalla**: scarica da `lemonade-sdk/llamacpp-rocm` il pacchetto `llama-b<N>-windows-rocm-<gfx>-x64.zip` adatto alla tua scheda (RX 9070 XT = gfx120X), che include le librerie ROCm, sostituisce il contenuto di `C:\llama\rocm` e lo mette in uso. A fine installazione il log dice se la GPU e' stata rilevata. Poi il tuning puo' confrontare ROCm e Vulkan.

## 31. Download e valore «misurati» (v1.1.31)
Durante l'installazione di un motore Impostazioni mostra una barra con la percentuale scaricata e i MB. Nel Deploy il numero «misurati» e' la decodifica in t/s dell'ultimo tuning; se un salvataggio e' precedente alla correzione e lo storico non lo contiene, il numero non appare finche' non si rifa un tuning (i parametri restano validi).

## 32. Seguire il tuning (v1.1.32)
La barra sotto i grafici mostra la prova in corso, il log del motore in diretta e un avviso se il motore e' muto da piu' di 45 s. Se la VRAM supera il 92% il log del tuning lo segnala: su Windows la velocita' crolla perche' le allocazioni finiscono nella RAM condivisa; usare cache KV q4_0, un contesto minore o un modello piu' leggero.

## 33. Tuning con piu' motori e stop (v1.1.33)
Con 2+ build GPU installate e l'opzione «Prova anche gli altri motori» il tuning inizia con una gara rapida tra i motori (una misura ciascuno) e prosegue solo con il piu' veloce; l'applicazione cambia anche il motore se serve. Il pulsante «Ferma il tuning» nella barra di progresso annulla un tuning bloccato e ferma il modello. Se tutte le combinazioni vengono saltate per «VRAM insufficiente» nonostante il modello giri, riporta le righe `KV` del log del motore.

## 34. Motore nella barra (v1.1.34)
Il nome al centro della barra di progresso e' il motore usato dal tuning in quel momento; segue il confronto tra motori e, alla fine, indica quello consigliato.

## 35. Risultati e cambio modello (v1.1.35)
Cambiando modello nel Tuning, o avviando un nuovo tuning, i risultati e il confronto prima/dopo precedenti spariscono; durante il tuning il pannello «Effetto del tuning» indica che e' in corso e mostra il nuovo confronto alla fine.

## 36. Grafici in tempo reale (v1.1.36)
Durante il tuning, «Effetto del tuning» si anima: ogni prova conclusa aggiunge una barra ai grafici di decodifica, prefill e TTFT (tratteggio = baseline, grigio = baseline, verde = migliore finora, blu = altre). Passando il mouse su una barra si vede la prova. Le prove fallite sono contate a parte. A tuning concluso il pannello torna al confronto prima/dopo finale.

## 37. Motori personalizzati (v1.1.37)
Impostazioni → Motori di inferenza → **Aggiungi un motore personalizzato**: scrivi un nome (es. «Fork RDNA4») e scegli con Sfoglia il suo `llama-server.exe`, poi **Aggiungi motore**. Nell'elenco clicca **Usa come motore** per attivarlo (vale per Deploy e Tuning); **Rimuovi** lo toglie dall'elenco senza cancellare i file. Nel Tuning, con l'opzione «Prova anche gli altri motori», il motore personalizzato entra nella gara rapida tra i motori se vede la GPU. I binari del fork RDNA4 non sono distribuiti: va compilato seguendo la sua guida (HIP SDK 7.1/7.2 o Vulkan).
