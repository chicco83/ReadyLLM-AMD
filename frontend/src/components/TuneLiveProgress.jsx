import { useState, useEffect } from 'react'
import { useI18n } from '../i18n/I18nContext'

// [2026-10-01 v1.1.19] Barra di progresso del tuning sempre visibile nella pagina Monitoraggio (qualunque passaggio sia aperto:
// Deploy o Tuning). Interroga GET /api/tune/active ogni 2 s: se c'e' un tuning in corso mostra fase, prove completate/totale
// stimato e, a richiesta, le ultime righe del log. Se non c'e' nessun tuning non occupa spazio.
export default function TuneLiveProgress({ targetId }) {
  const { t } = useI18n()
  const [job, setJob] = useState(null)
  const [showLog, setShowLog] = useState(true)
  // [2026-10-02 v1.1.22] Dettaglio del motore (backend + versione, es. «ROCM — version: 0.5.0-dev (build 11327, commit ...)»)
  // mostrato al centro della barra; si legge una volta sola quando parte un tuning (GET /api/target/<id>/engine).
  const [engineInfo, setEngineInfo] = useState('')

  useEffect(() => {
    if (!targetId) return
    let stop = false
    async function tick() {
      try {
        const r = await fetch(`/api/tune/active?target_id=${targetId}`)
        const d = await r.json()
        if (!stop) setJob((d.jobs || [])[0] || null)
      } catch { /* backend non raggiungibile: si riprova */ }
    }
    tick()
    const id = setInterval(tick, 2000)
    return () => { stop = true; clearInterval(id) }
  }, [targetId])

  const running = !!job
  useEffect(() => {
    if (!running || !targetId || engineInfo) return
    fetch(`/api/target/${targetId}/engine`).then(r => r.json()).then(d => {
      const be = (d.backend || '').toUpperCase()
      const ver = d.version || ''
      setEngineInfo([be, ver].filter(Boolean).join(' — '))
    }).catch(() => {})
  }, [running, targetId])

  if (!job) return null
  const pr = job.progress || { done: 0, total: 1, phase: '' }
  const pct = Math.min(99, Math.round((pr.done / Math.max(pr.total, 1)) * 100))
  return (
    <div className="mt-6 bg-card rounded-xl p-4 border-2 border-blue/50">
      <div className="flex items-center gap-3 mb-2 text-sm">
        <span className="w-2 h-2 rounded-full bg-green animate-pulse" />
        <span className="font-semibold">{t('tune.live')}</span>
        <span className="text-gray">{pr.phase}</span>
        <span className="ml-auto text-gray">{pr.done}/{pr.total} {t('tune.liveStep')} · {pct}%</span>
        <button className="text-xs text-blue underline" onClick={() => setShowLog(v => !v)}>
          {showLog ? t('tune.liveHideLog') : t('tune.liveShowLog')}
        </button>
      </div>
      {/* Versione precedente: barra sottile h-3 senza testo. Ora piu' alta, con il motore al centro. */}
      <div className="relative h-7 rounded-full bg-bg overflow-hidden">
        <div className="h-full bg-blue/60 transition-all duration-500" style={{ width: `${pct}%` }} />
        <div className="absolute inset-0 flex items-center justify-center px-3 text-xs font-semibold text-fg truncate" title={engineInfo}>
          {engineInfo}
        </div>
      </div>
      {showLog && (
        <div className="mt-3 bg-bg rounded-lg p-3 font-mono text-xs text-fg/80 space-y-0.5 max-h-40 overflow-auto">
          {(job.last_logs || []).map((l, i) => (
            <div key={i}><span className="text-gray/50">[{l.t}] </span>{l.msg}</div>
          ))}
        </div>
      )}
    </div>
  )
}
