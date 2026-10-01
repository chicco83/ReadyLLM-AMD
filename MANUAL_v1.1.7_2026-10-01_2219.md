# MANUALE — ReadyLLM-AMD

Versione: **1.1.7** — 2026-10-01 22:19

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
