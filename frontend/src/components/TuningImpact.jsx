import { useState, useEffect } from 'react'
import { useI18n } from '../i18n/I18nContext'
import BeforeAfter from './BeforeAfter'

// [2026-10-02 v1.1.29] Pannello «Effetto del tuning» del Monitoraggio: al posto del grafico dei token per giorno (poco utile) mostra
// il PRIMA e il DOPO dell'ultimo tuning concluso della macchina (grafico a barre decodifica/prefill/TTFT, motore, parametri)
// e, sotto, una tabella compatta degli ultimi tuning. Dati da GET /api/tune/history (storico completo); si aggiorna ogni 30 s.
const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleString() : '--'
const short = (m) => (m || '').split(/[\\/]/).pop()

export default function TuningImpact({ targetId }) {
  const { t } = useI18n()
  const [entries, setEntries] = useState(null)

  useEffect(() => {
    if (!targetId) return
    const load = () => fetch(`/api/tune/history?target_id=${targetId}`).then(r => r.json())
      .then(d => setEntries((d.entries || []).filter(e => e.status === 'success'))).catch(() => {})
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [targetId])

  const last = entries && entries[0]
  return (
    <div className="bg-card rounded-lg p-4 border border-gray/30 mt-4">
      <div className="text-sm font-semibold mb-1">{t('impact.title')}</div>
      {!last ? (
        <div className="text-sm text-gray py-6">{entries === null ? '…' : t('impact.empty')}</div>
      ) : (
        <>
          <div className="text-xs text-gray mb-1 flex flex-wrap gap-x-4">
            <span title={last.model}>{short(last.model)}</span>
            <span>{fmtDate(last.ts_start)}</span>
            <span>{t('history.goal')}: {last.goal}</span>
            {last.gain_pct != null && (
              <span className={last.gain_pct >= 0 ? 'text-green font-semibold' : 'text-red font-semibold'}>
                {last.gain_pct > 0 ? '+' : ''}{last.gain_pct}% {t('impact.decode')}
              </span>
            )}
          </div>
          <BeforeAfter baseline={last.baseline} best={last.best} />
          {entries.length > 1 && (
            <table className="w-full text-xs mt-4">
              <thead className="text-gray text-left"><tr>
                <th className="py-1 pr-2 font-medium">{t('history.colDate')}</th>
                <th className="py-1 pr-2 font-medium">{t('history.colModel')}</th>
                <th className="py-1 pr-2 font-medium">{t('history.colEngine')}</th>
                <th className="py-1 pr-2 font-medium text-right">{t('impact.before')} → {t('impact.after')} (t/s)</th>
                <th className="py-1 font-medium text-right">{t('history.colGain')}</th>
              </tr></thead>
              <tbody>
                {entries.slice(0, 6).map(e => (
                  <tr key={e.id} className="border-t border-gray/10">
                    <td className="py-1 pr-2 whitespace-nowrap">{fmtDate(e.ts_start)}</td>
                    <td className="py-1 pr-2 max-w-[18rem] truncate" title={e.model}>{short(e.model)}</td>
                    <td className="py-1 pr-2 uppercase">{e.best?.engine?.backend || e.engine?.backend || '?'}</td>
                    <td className="py-1 pr-2 text-right">{e.baseline?.metrics?.decode ?? '--'} → <b>{e.best?.metrics?.decode ?? '--'}</b></td>
                    <td className={`py-1 text-right ${e.gain_pct == null ? 'text-gray' : e.gain_pct >= 0 ? 'text-green' : 'text-red'}`}>
                      {e.gain_pct == null ? '--' : `${e.gain_pct > 0 ? '+' : ''}${e.gain_pct}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}
