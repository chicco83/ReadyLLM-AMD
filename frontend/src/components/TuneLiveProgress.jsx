import { useState, useEffect, useRef } from 'react'
import { useI18n } from '../i18n/I18nContext'
import BeforeAfter from './BeforeAfter'

// [2026-10-01 v1.1.19] Barra di progresso del tuning sempre visibile nella pagina Monitoraggio (qualunque passaggio sia aperto:
// Deploy o Tuning). Interroga GET /api/tune/active ogni 2 s: fase, prove completate/totale stimato, motore al centro (v1.1.22)
// e, a richiesta, le ultime righe del log.
// [2026-10-02 v1.1.23] A fine tuning la barra NON scompare piu': resta in stato «concluso» (verde, 100%) o «fallito» (rosso) con
// l'esito, la configurazione consigliata e il pulsante «Vedi risultati» (porta al passaggio Tuning); si chiude con la X.
// Versione precedente: if (!job) return null  (la barra spariva appena il tuning finiva)
export default function TuneLiveProgress({ targetId }) {
  const { t } = useI18n()
  const [job, setJob] = useState(null)
  const [final, setFinal] = useState(null)       // ultimo job concluso (success|failed)
  const [dismissed, setDismissed] = useState('') // job_id chiuso dall'utente
  const [showLog, setShowLog] = useState(true)
  const [engineInfo, setEngineInfo] = useState('')
  const lastId = useRef('')
  // [2026-10-02 v1.1.32] log del motore (llama-server) in diretta durante il tuning + avviso se non arriva nulla da un po':
  // un avvio lento (es. ROCm che compila i kernel al primo uso, VRAM quasi piena) prima sembrava un blocco.
  const [engLog, setEngLog] = useState([])
  const [quietSec, setQuietSec] = useState(0)
  const lastLogRef = useRef({ text: '', ts: Date.now() })

  useEffect(() => {
    if (!targetId) return
    let stop = false
    async function tick() {
      try {
        const r = await fetch(`/api/tune/active?target_id=${targetId}`)
        const d = await r.json()
        const cur = (d.jobs || [])[0] || null
        if (stop) return
        if (cur) { lastId.current = cur.job_id; setFinal(null); setJob(cur); return }
        setJob(null)
        // nessun tuning attivo: se ne esiste uno concluso lo si mostra come stato finale
        const l = await (await fetch(`/api/tune/last?target_id=${targetId}`)).json()
        if (!stop && l.job && (l.job.status === 'success' || l.job.status === 'failed')) setFinal(l.job)
      } catch { /* backend non raggiungibile: si riprova */ }
    }
    tick()
    const id = setInterval(tick, 2000)
    return () => { stop = true; clearInterval(id) }
  }, [targetId])

  useEffect(() => {
    if (!targetId || !job) { setEngLog([]); setQuietSec(0); return }
    let stop = false
    async function tickLog() {
      try {
        const d = await (await fetch(`/api/deploy/log?target_id=${targetId}&lines=10`)).json()
        if (stop) return
        const lines = d.lines || []
        setEngLog(lines)
        const text = lines.join('\n')
        if (text !== lastLogRef.current.text) lastLogRef.current = { text, ts: Date.now() }
        setQuietSec(Math.round((Date.now() - lastLogRef.current.ts) / 1000))
      } catch { /* ignora */ }
    }
    tickLog()
    const id = setInterval(tickLog, 3000)
    return () => { stop = true; clearInterval(id) }
  }, [targetId, !!job])

  const shown = job || (final && final.job_id !== dismissed ? final : null)
  useEffect(() => {
    if (!shown || !targetId || engineInfo) return
    fetch(`/api/target/${targetId}/engine`).then(r => r.json()).then(d => {
      setEngineInfo([(d.backend || '').toUpperCase(), d.version || ''].filter(Boolean).join(' — '))
    }).catch(() => {})
  }, [!!shown, targetId])

  if (!shown) return null
  const done = !job
  const failed = done && shown.status === 'failed'
  const pr = shown.progress || { done: 0, total: 1, phase: '' }
  const pct = done ? 100 : Math.min(99, Math.round((pr.done / Math.max(pr.total, 1)) * 100))
  const logs = done ? (shown.logs || []).slice(-8) : (shown.last_logs || [])
  const color = failed ? 'bg-red/60' : done ? 'bg-green/60' : 'bg-blue/60'
  return (
    <div className={`mt-6 bg-card rounded-xl p-4 border-2 ${failed ? 'border-red/60' : done ? 'border-green/60' : 'border-blue/50'}`}>
      <div className="flex items-center gap-3 mb-2 text-sm">
        <span className={`w-2 h-2 rounded-full ${done ? (failed ? 'bg-red' : 'bg-green') : 'bg-green animate-pulse'}`} />
        <span className="font-semibold">{done ? (failed ? t('tune.liveFailed') : t('tune.liveDone')) : t('tune.live')}</span>
        {!done && <span className="text-gray">{pr.phase}</span>}
        <span className="ml-auto text-gray">{done ? '' : `${pr.done}/${pr.total} ${t('tune.liveStep')} · ${pct}%`}</span>
        <button className="text-xs text-blue underline" onClick={() => setShowLog(v => !v)}>
          {showLog ? t('tune.liveHideLog') : t('tune.liveShowLog')}
        </button>
        {done && (
          <>
            <button className="text-xs px-2 py-1 rounded bg-blue text-bg font-semibold"
              onClick={() => window.dispatchEvent(new Event('readyllm:goto-tune'))}>{t('tune.liveResults')}</button>
            <button className="text-gray hover:text-fg" title="OK" onClick={() => setDismissed(shown.job_id)}>✕</button>
          </>
        )}
      </div>
      <div className="relative h-7 rounded-full bg-bg overflow-hidden">
        <div className={`h-full ${color} transition-all duration-500`} style={{ width: `${pct}%` }} />
        <div className="absolute inset-0 flex items-center justify-center px-3 text-xs font-semibold text-fg truncate" title={engineInfo}>
          {engineInfo}
        </div>
      </div>
      {done && (
        <div className="mt-2 text-xs">
          {failed
            ? <span className="text-red">{shown.error || ''}</span>
            : <span className="text-green">{shown.best?.label} · {shown.best?.metrics?.decode} t/s</span>}
        </div>
      )}
      {/* [2026-10-02 v1.1.32] configurazione in prova + log del motore in diretta */}
      {!done && pr.current && (
        <div className="mt-2 text-xs text-gray break-all"><b className="text-fg">{t('tune.liveCurrent')}:</b> {pr.current}</div>
      )}
      {!done && showLog && (
        <div className="mt-2">
          <div className="text-xs text-gray mb-1 flex items-center gap-2">
            <span>{t('tune.liveEngineLog')}</span>
            {quietSec >= 45 && <span className="text-yellow">⚠ {t('tune.liveQuiet', { s: quietSec })}</span>}
          </div>
          <div className="bg-bg rounded-lg p-2 font-mono text-[11px] text-fg/70 max-h-28 overflow-auto space-y-0.5">
            {engLog.length === 0 ? <div className="text-gray/50">…</div> : engLog.map((l, i) => <div key={i} className="break-all">{l}</div>)}
          </div>
        </div>
      )}
      {/* [2026-10-02 v1.1.27] a fine tuning: grafico prima vs dopo, a colpo d'occhio */}
      {done && !failed && <BeforeAfter baseline={shown.baseline} best={shown.best} />}
      {showLog && (
        <div className="mt-3 bg-bg rounded-lg p-3 font-mono text-xs text-fg/80 space-y-0.5 max-h-40 overflow-auto">
          {logs.map((l, i) => (
            <div key={i}><span className="text-gray/50">[{l.t}] </span>{l.msg}</div>
          ))}
        </div>
      )}
    </div>
  )
}
