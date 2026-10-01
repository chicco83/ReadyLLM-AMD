import { useState, useEffect, useMemo } from 'react'
import { useI18n } from '../i18n/I18nContext'

// [2026-10-02 v1.1.24] Storico delle ottimizzazioni: una riga per ogni tuning concluso (GET /api/tune/history), con modello,
// motore (backend + versione), GPU, prestazioni (decodifica / prefill / TTFT), guadagno rispetto alla baseline, durata.
// Clic sulla riga: dettaglio con parametri consigliati e baseline. Filtri per testo e per esito; eliminazione di una voce.

const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleString() : '--'
const fmtDur = (s) => s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s || 0}s`
const argsOf = (cfg) => Object.entries(cfg || {}).map(([k, v]) => `--${k} ${v}`).join(' ')

function Metric({ label, value, unit }) {
  return (
    <div className="bg-bg rounded-lg p-2">
      <div className="text-gray text-[11px]">{label}</div>
      <div className="font-semibold">{value ?? '--'}{value != null && unit ? <span className="text-xs font-normal ml-1">{unit}</span> : null}</div>
    </div>
  )
}

export default function History({ targetId }) {
  const { t } = useI18n()
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)
  const [onlyThis, setOnlyThis] = useState(true)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('all')
  const [open, setOpen] = useState('')

  function load() {
    setLoading(true)
    fetch(`/api/tune/history${onlyThis && targetId ? `?target_id=${targetId}` : ''}`)
      .then(r => r.json()).then(d => setEntries(d.entries || [])).catch(() => {}).finally(() => setLoading(false))
  }
  useEffect(load, [targetId, onlyThis])

  async function remove(id) {
    if (!window.confirm(t('history.confirmDelete'))) return
    await fetch(`/api/tune/history/${id}`, { method: 'DELETE' })
    load()
  }

  const shown = useMemo(() => entries.filter(e => {
    if (status !== 'all' && e.status !== status) return false
    const hay = `${e.model} ${e.engine?.backend} ${e.engine?.version} ${e.gpu?.name} ${e.target_name}`.toLowerCase()
    return hay.includes(q.toLowerCase())
  }), [entries, q, status])

  // miglior decodifica tra le voci mostrate: evidenzia il record
  const bestDecode = Math.max(0, ...shown.map(e => e.best?.metrics?.decode || 0))

  return (
    <div>
      <h1 className="text-2xl font-bold mb-1">{t('history.title')}</h1>
      <p className="text-sm text-gray mb-4">{t('history.hint')}</p>

      <div className="flex flex-wrap items-center gap-3 mb-4 text-sm">
        <input value={q} onChange={e => setQ(e.target.value)} placeholder={t('history.search')}
          className="bg-card border border-gray/30 rounded-lg px-3 py-1.5 w-72" />
        <select value={status} onChange={e => setStatus(e.target.value)} className="bg-card border border-gray/30 rounded-lg px-2 py-1.5">
          <option value="all">{t('history.all')}</option>
          <option value="success">{t('history.ok')}</option>
          <option value="failed">{t('history.ko')}</option>
        </select>
        <label className="flex items-center gap-2 text-gray">
          <input type="checkbox" checked={onlyThis} onChange={e => setOnlyThis(e.target.checked)} />
          {t('history.onlyThis')}
        </label>
        <button onClick={load} className="px-3 py-1.5 rounded-lg border border-blue/50 text-blue hover:bg-blue/10">{t('history.refresh')}</button>
        <span className="ml-auto text-gray">{shown.length} {t('history.count')}</span>
      </div>

      <div className="bg-card rounded-xl border border-gray/30 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-gray text-xs text-left">
            <tr className="border-b border-gray/20">
              {['colDate', 'colModel', 'colEngine', 'colGpu', 'colDecode', 'colPrefill', 'colGain', 'colCtx', 'colDur', ''].map((c, i) => (
                <th key={i} className="px-3 py-2 font-medium whitespace-nowrap">{c ? t('history.' + c) : ''}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr><td colSpan={10} className="px-3 py-8 text-center text-gray">{loading ? '…' : t('history.empty')}</td></tr>
            )}
            {shown.map(e => {
              const m = e.best?.metrics || {}
              const ko = e.status !== 'success'
              return (
                <>
                  <tr key={e.id} onClick={() => setOpen(open === e.id ? '' : e.id)}
                    className="border-b border-gray/10 hover:bg-white/[0.03] cursor-pointer">
                    <td className="px-3 py-2 whitespace-nowrap">{fmtDate(e.ts_start)}</td>
                    <td className="px-3 py-2 max-w-[22rem] truncate" title={e.model}>{e.model.split(/[\\/]/).pop()}
                      {e.model_size_gb ? <span className="text-gray text-xs ml-1">{e.model_size_gb} GB</span> : null}</td>
                    <td className="px-3 py-2">
                      <span className="px-2 py-0.5 rounded bg-purple/15 text-purple text-xs font-semibold uppercase">{e.engine?.backend || e.engine?.type || '?'}</span>
                      <div className="text-xs text-gray max-w-[16rem] truncate" title={e.engine?.version}>{e.engine?.version}</div>
                    </td>
                    <td className="px-3 py-2 text-xs max-w-[12rem] truncate" title={e.gpu?.name}>{e.gpu?.name || '--'}</td>
                    {ko
                      ? <td colSpan={2} className="px-3 py-2 text-red text-xs">{t('history.ko')}: {e.error}</td>
                      : <>
                          <td className={`px-3 py-2 font-semibold ${m.decode === bestDecode && bestDecode > 0 ? 'text-green' : ''}`}>{m.decode} <span className="text-xs font-normal text-gray">t/s</span></td>
                          <td className="px-3 py-2">{m.prefill} <span className="text-xs text-gray">t/s</span></td>
                        </>}
                    <td className={`px-3 py-2 ${e.gain_pct == null ? 'text-gray' : e.gain_pct >= 0 ? 'text-green' : 'text-red'}`}>
                      {e.gain_pct == null ? '--' : `${e.gain_pct > 0 ? '+' : ''}${e.gain_pct}%`}</td>
                    <td className="px-3 py-2">{e.ctx_size}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{fmtDur(e.duration_s)}</td>
                    <td className="px-3 py-2"><button className="text-gray hover:text-red" title={t('history.delete')}
                      onClick={(ev) => { ev.stopPropagation(); remove(e.id) }}>🗑</button></td>
                  </tr>
                  {open === e.id && (
                    <tr key={e.id + 'd'} className="border-b border-gray/20 bg-bg/40">
                      <td colSpan={10} className="px-4 py-3 space-y-3">
                        <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
                          <Metric label={t('history.colDecode')} value={m.decode} unit="t/s" />
                          <Metric label={t('history.colPrefill')} value={m.prefill} unit="t/s" />
                          <Metric label="TTFT" value={m.ttft_ms} unit="ms" />
                          <Metric label={t('history.gpuUtil')} value={m.gpu_util} unit="%" />
                          <Metric label={t('history.score')} value={e.best?.score} />
                          <Metric label={t('history.trials')} value={e.trials} />
                        </div>
                        <div className="text-xs grid md:grid-cols-2 gap-3">
                          <div>
                            <div className="text-gray mb-1">{t('history.bestArgs')}</div>
                            <code className="block bg-bg rounded p-2 break-all select-all">{argsOf(e.best?.config) || '--'}</code>
                          </div>
                          <div>
                            <div className="text-gray mb-1">{t('history.baseline')}{e.baseline?.metrics ? ` — ${e.baseline.metrics.decode} t/s / ${e.baseline.metrics.prefill} t/s` : ''}</div>
                            <code className="block bg-bg rounded p-2 break-all">{argsOf(e.baseline?.config) || '--'}</code>
                          </div>
                        </div>
                        <div className="text-xs text-gray flex flex-wrap gap-x-6 gap-y-1">
                          <span>{t('history.machine')}: {e.target_name} ({e.os})</span>
                          <span>{t('history.goal')}: {e.goal}</span>
                          <span>VRAM: {e.gpu?.vram_gb} GB</span>
                          <span className="truncate max-w-full" title={e.engine?.path}>{t('history.path')}: {e.engine?.path}</span>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
