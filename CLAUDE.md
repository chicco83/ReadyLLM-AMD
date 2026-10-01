# CLAUDE.md — ReadyLLM-AMD

Versione documento: 1.1.9 — 2026-10-01 22:21

## Regole obbligatorie per ogni sessione (locale o cloud)

0. **Repository/ramo predefinito**: `chicco83/ReadyLLM-AMD`, ramo `main`. Fare commit e push direttamente su `main`
   (`git pull --rebase origin main` prima, poi `git push origin main`); non creare altri rami né PR salvo richiesta esplicita.

1. **Versioning**: ogni revisione di codice o documentazione incrementa il numero di versione
   (semver: x.y.z). La versione, con data e ora, va scritta **in cima al contenuto** del file e
   **nel nome** dei documenti versionati (es. `MANUAL_v1.1.9_2026-10-01_2221.md`).
2. **Tre documenti sempre aggiornati** a ogni attività:
   - `CONTEXT_v<versione>_<data>_<ora>.md` — contesto del progetto, architettura, decisioni.
   - `CHANGELOG.md` — voci numerate in ordine, una per revisione (data/ora, versione, modifiche).
   - `MANUAL_v<versione>_<data>_<ora>.md` — manuale d'uso.
   Quando la versione cambia, rinominare (`git mv`) CONTEXT e MANUAL con la nuova versione/data/ora.
3. **Codice**: se si corregge una parte, lasciare la sezione precedente **come commento con la data**;
   spiegazioni di funzionamento come commenti nelle sezioni del codice; quando si fornisce una
   correzione, riportare sempre il file completo, non solo la parte modificata.
4. **Lingua**: commenti, messaggi e documentazione in **italiano**. Il cinese resta solo nella
   localizzazione `zh` (`frontend/src/i18n/translations.js`) e in `README.zh-CN.md`.
5. **Fine attività** (più sessioni lavorano sugli stessi documenti):
   1. `git pull --rebase`
   2. `git add` **per nome** solo dei file `.md` modificati da questa sessione (mai `git add -A` / `git add .`)
   3. commit con messaggio descrittivo (mai ".") e `git push` subito
   4. file di codice si committano a parte (commit separato dai `.md`)
   5. conflitto su un `.md`: unire a mano tenendo entrambe le modifiche (CHANGELOG: entrambe le voci, numerate in ordine)
   6. allineare la copia locale con il commit fatto

## Struttura rapida

- `backend/` FastAPI (Python): `app/api` (router), `app/services` (motori, installer, collectors, tuner…), `app/models/target.py` (configurazione macchine, salvata in `~/.model-deploy-assistant/targets.json`)
- `frontend/` React + Vite + Tailwind; i18n in `src/i18n` (lingue: en, it, zh)
- Dettagli: vedere `CONTEXT_*.md` e `MANUAL_*.md`
