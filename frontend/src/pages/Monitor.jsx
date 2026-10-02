import { useWebSocket } from '../hooks/useWebSocket'
import { XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, AreaChart, BarChart, Bar, Legend } from 'recharts'
import { useState, useEffect } from 'react'
import Deploy from './Deploy'
import TuneLiveProgress from '../components/TuneLiveProgress'
import TuningImpact from '../components/TuningImpact'
import SpecCard from '../components/SpecCard'
import { useI18n } from '../i18n/I18nContext'

const MAX_POINTS = 60

// Unita' progressive: 1.8M / 208K / 950, per leggere piu' facilmente i numeri grandi
function fmtTokens(n) {
  if (n == null) return null
  if (n >= 1e6) return `${(n / 1e6).toFixed(n % 1e6 ? 1 : 0)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(n % 1e3 ? 1 : 0)}K`
  return String(n)
}

function MetricCard({ label, value, unit, color }) {
  return (
    <div className="bg-card rounded-lg p-3 border border-gray/30">
      <div className="text-gray text-xs mb-1">{label}</div>
      <div className={`text-xl font-bold ${color}`}>
        {value ?? '--'}
        {unit && <span className="text-sm font-normal ml-1">{unit}</span>}
      </div>
    </div>
  )
}

function ChartPanel({ title, data, dataKey, color, unit }) {
  return (
    <div className="bg-card rounded-lg p-4 border border-gray/30">
      <div className="text-sm font-semibold mb-2">{title}</div>
      <ResponsiveContainer width="100%" height={140}>
        <AreaChart data={data} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
          <defs>
            <linearGradient id={`grad-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.3} />
              <stop offset="95%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#3a3a4c" />
          <XAxis dataKey="idx" hide />
          <YAxis stroke="#6c7086" fontSize={10} tickLine={false} width={35} />
          <Tooltip
            contentStyle={{ background: '#181825', border: '1px solid #45475a', borderRadius: 8, fontSize: 12 }}
            labelFormatter={() => ''}
            formatter={(val) => [`${val}${unit}`, title]}
          />
          <Area type="monotone" dataKey={dataKey} stroke={color} fill={`url(#grad-${dataKey})`} strokeWidth={2} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function Monitor({ targetId, target }) {
  const { data, connected } = useWebSocket(
    targetId ? `ws://localhost:8000/api/monitor/ws?target_id=${targetId}` : ''
  )
  const { t } = useI18n()
  const [history, setHistory] = useState({ speed: [], cache: [], spec: [] })
  const [tokenStats, setTokenStats] = useState([])
  const [tokenTotal, setTokenTotal] = useState({ prompt: 0, completion: 0, total: 0 })

  // Statistiche d'uso dei token (per giorno + cumulato, persistite dal backend): all'ingresso le carica, aggiorna ogni 60s
  useEffect(() => {
    if (!targetId) return
    const load = () => {
      fetch(`/api/monitor/token-stats?target_id=${targetId}&days=14`)
        .then((r) => r.json())
        .then(setTokenStats)
        .catch(() => {})
      fetch(`/api/monitor/token-total?target_id=${targetId}`)
        .then((r) => r.json())
        .then(setTokenTotal)
        .catch(() => {})
    }
    load()
    const timer = setInterval(load, 60000)
    return () => clearInterval(timer)
  }, [targetId])

  // Cambiando macchina target si azzera la cronologia
  useEffect(() => {
    setHistory({ speed: [], cache: [], spec: [] })
  }, [targetId])

  // Raccolta ogni 10s; si aggiunge un punto solo se il valore e' cambiato, altrimenti non si disegna
  useEffect(() => {
    if (!data?.metrics) return
    const m = data.metrics
    setHistory((prev) => {
      const withPoint = (arr, value) => {
        if (arr.length > 0 && arr[arr.length - 1].value === value) return arr
        return [...arr, { idx: arr.length, value }].slice(-MAX_POINTS)
      }
      const speed = withPoint(prev.speed, m.completion_speed)
      const cache = withPoint(prev.cache, m.cache_hit_rate)
      const spec = withPoint(prev.spec, m.spec_accept_rate)
      if (speed === prev.speed && cache === prev.cache && spec === prev.spec) return prev
      return { speed, cache, spec }
    })
  }, [data])

  const h = history
  const gpu = data?.gpu || {}
  const cpuMem = data?.cpu_mem || {}
  const metrics = data?.metrics || {}

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">{t('monitor.title')}</h1>
        <div className={`flex items-center gap-2 text-sm ${connected ? 'text-green' : 'text-red'}`}>
          <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green animate-pulse' : 'bg-red'}`} />
          {connected ? t('monitor.connected') : t('monitor.disconnected')}
        </div>
      </div>

      {/* GPU + CPU affiancate */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div className="bg-card rounded-xl p-4 border border-gray/30">
          <div className="text-sm font-semibold text-yellow mb-3">GPU</div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div className="col-span-2 truncate"><span className="text-gray">{t('monitor.model')}</span> {gpu.name || '--'}</div>
            <div><span className="text-gray">{t('monitor.utilization')}</span> {gpu.utilization != null ? `${gpu.utilization}%` : '--'}</div>
            <div><span className="text-gray">{t('monitor.temp')}</span> {gpu.temperature ? `${gpu.temperature}°C` : '--'}</div>
            <div><span className="text-gray">{t('monitor.vram')}</span> {gpu.memory_used_gb != null ? `${gpu.memory_used_gb}G / ${gpu.memory_total_gb}G` : '--'}</div>
            <div><span className="text-gray">{t('monitor.power')}</span> {gpu.power ? `${gpu.power}W` : '--'}</div>
          </div>
          {gpu.memory_pct != null && (
            <div className="mt-2">
              <div className="h-2 bg-gray/30 rounded-full overflow-hidden">
                <div className="h-full bg-blue rounded-full transition-all" style={{ width: `${gpu.memory_pct}%` }} />
              </div>
            </div>
          )}
        </div>

        <div className="bg-card rounded-xl p-4 border border-gray/30">
          <div className="text-sm font-semibold text-green mb-3">{t('monitor.cpuMem')}</div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div><span className="text-gray">{t('monitor.cpu')}</span> {cpuMem.cpu_pct != null ? `${cpuMem.cpu_pct}%` : '--'}</div>
            <div><span className="text-gray">{t('monitor.memory')}</span> {cpuMem.memory_used_gb != null ? `${cpuMem.memory_used_gb}G / ${cpuMem.memory_total_gb}G` : '--'}</div>
          </div>
          {/* [2026-10-02 v1.1.38] File di paging e memoria impegnata (commit): spiega le scritture su disco anche con RAM libera */}
          {cpuMem.pagefile && cpuMem.pagefile.drive && (
            <div className={`mt-2 text-xs ${cpuMem.pagefile.media === 'HDD' && cpuMem.pagefile.used_gb > 0.5 ? 'text-yellow' : 'text-gray'}`}>
              {t('monitor.pagefile')}: {cpuMem.pagefile.used_gb}/{cpuMem.pagefile.size_gb} GB {t('monitor.on')} {cpuMem.pagefile.drive}
              {cpuMem.pagefile.media ? ` (${cpuMem.pagefile.media})` : ''}
              {cpuMem.pagefile.commit_limit_gb ? ` · ${t('monitor.commit')} ${cpuMem.pagefile.commit_used_gb}/${cpuMem.pagefile.commit_limit_gb} GB` : ''}
              {cpuMem.pagefile.media === 'HDD' && cpuMem.pagefile.used_gb > 0.5 ? ` ⚠ ${t('monitor.pagefileSlow')}` : ''}
            </div>
          )}
          <div className="mt-2 space-y-1">
            <div className="flex items-center gap-2 text-xs">
              <span className="text-gray w-8">CPU</span>
              <div className="flex-1 h-2 bg-gray/30 rounded-full overflow-hidden">
                <div className="h-full bg-green rounded-full transition-all" style={{ width: `${cpuMem.cpu_pct || 0}%` }} />
              </div>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <span className="text-gray w-8">{t('monitor.mem')}</span>
              <div className="flex-1 h-2 bg-gray/30 rounded-full overflow-hidden">
                <div className="h-full bg-yellow rounded-full transition-all" style={{ width: `${cpuMem.memory_pct || 0}%` }} />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Valori delle metriche di inferenza */}
      <div className="grid grid-cols-3 lg:grid-cols-6 gap-3 mb-4">
        <MetricCard label="Prompt Tokens" value={fmtTokens(metrics.prompt_tokens)} color="text-blue" />
        <MetricCard label={t('monitor.completionTokens')} value={fmtTokens(metrics.completion_tokens)} color="text-green" />
        <MetricCard label={t('monitor.genSpeed')} value={metrics.completion_speed} unit="t/s" color="text-green" />
        <MetricCard label={t('monitor.promptSpeed')} value={metrics.prompt_speed} unit="t/s" color="text-blue" />
        <MetricCard label={t('monitor.cacheHit')} value={metrics.cache_hit_rate} unit="%" color="text-purple" />
        <MetricCard label={t('monitor.specAccept')} value={metrics.spec_accept_rate} unit="%" color="text-teal" />
      </div>

      {/* [2026-10-02 v1.1.29] decodifica speculativa in uso (MTP / ngram) e dove vive */}
      <SpecCard targetId={targetId} acceptRate={metrics.spec_accept_rate} />

      {/* Grafici a curve */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartPanel title={t('monitor.chartSpeed')} data={h.speed} dataKey="value" color="#a6e3a1" unit=" t/s" />
        <ChartPanel title={t('monitor.chartCache')} data={h.cache} dataKey="value" color="#cba6f7" unit="%" />
        <ChartPanel title={t('monitor.chartSpec')} data={h.spec} dataKey="value" color="#94e2d5" unit="%" />
      </div>

      {/* [2026-10-02 v1.1.29] Al posto del grafico «Utilizzo dei token (per giorno)» (poco utile): prima/dopo dell'ultimo tuning.
          Versione precedente: BarChart prompt/completion per giorno con asse destro per l'output (dati ancora raccolti dal backend). */}
      <TuningImpact targetId={targetId} />

      {/* [2026-10-01 v1.1.19] barra di progresso del tuning, visibile anche guardando i grafici */}
      <TuneLiveProgress targetId={targetId} />

      {/* [2026-10-01 v1.1.12] Deploy e Tuning sotto il monitoraggio, come due passaggi in sequenza (1 -> 2):
          si osserva GPU/velocita' in tempo reale mentre si avvia il modello e si esegue il tuning. */}
      <div className="mt-8 border-t border-gray/30 pt-6">
        <Deploy targetId={targetId} target={target} embedded />
      </div>
    </div>
  )
}
