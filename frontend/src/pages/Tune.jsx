import { useState, useEffect, useRef } from 'react'
import ProgressPanel from '../components/ProgressPanel'
import BeforeAfter from '../components/BeforeAfter'
import { IconAlert, IconStar, IconCheck, IconX, IconBot } from '../components/Icons'
import { useI18n } from '../i18n/I18nContext'
import { readLastModel, writeLastModel } from '../lib/lastModel'

// ==================== Tuning automatico ====================

// [2026-10-02 v1.1.27] aggiunto 'coding' (contesti lunghi + codice generato); descrizioni con il caso d'uso
const GOALS = [
  'coding',
  'latency',
  'throughput',
  'prefill',
]

const CTX_PRESETS = [4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576]

function fmtCtx(c) {
  if (c >= 1048576) return `${(c / 1048576).toFixed(c % 1048576 ? 1 : 0)}M`
  if (c >= 1024) return `${Math.round(c / 1024)}K`
  return String(c)
}

// Selettore della lunghezza di contesto: valori predefiniti rapidi + inserimento libero di qualsiasi numero di token (non fisso)
function CtxPicker({ value, onChange }) {
  const { t } = useI18n()
  return (
    <div className="mb-4">
      <div className="flex flex-wrap gap-2 mb-2">
        {CTX_PRESETS.map(c => (
          <button key={c} type="button" onClick={() => onChange(c)}
            className={`px-3 py-1 rounded-lg text-sm border transition ${Number(value) === c ? 'border-blue bg-blue/20 text-blue' : 'border-gray/40 text-gray hover:bg-gray/10'}`}>
            {fmtCtx(c)}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray whitespace-nowrap">{t('tune.custom')}</span>
        <input type="number" min={1024} step={1024} value={value}
          onChange={e => onChange(Number(e.target.value) || 0)}
          className="flex-1 bg-bg border border-gray/40 rounded-lg px-3 py-1.5 text-fg text-sm" />
        <span className="text-xs text-gray">{t('tune.tokens')}</span>
      </div>
    </div>
  )
}

// [2026-10-01 v1.1.11] DEFAULT_BASELINE (draft-mtp / q4_0 / batch 4096, fisso) rimosso: la baseline ora e' la riga di parametri
// del Deploy per il modello selezionato (GET /api/tune/baseline), modificabile nel pannello.

function AutoTune({ targetId }) {
  const { t } = useI18n()
  const [models, setModels] = useState([])
  const [selected, setSelected] = useState('')
  const [goal, setGoal] = useState('coding')
  const [ctxSize, setCtxSize] = useState(8192)
  // [2026-10-01 v1.1.11] baseline: 'deploy' = parametri del Deploy (modificabili), 'engine' = predefiniti del motore, 'none' = nessuna
  // Versione precedente: const [useBaseline, setUseBaseline] = useState(true)
  const [baselineMode, setBaselineMode] = useState('deploy')
  const [baselineArgs, setBaselineArgs] = useState('')
  const [baselineInfo, setBaselineInfo] = useState(null) // {source, mtp:{model,build,allowed}}
  const [state, setState] = useState('idle')
  const [logs, setLogs] = useState([])
  const [results, setResults] = useState([])
  const [best, setBest] = useState(null)
  const [baseline, setBaseline] = useState(null)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // [2026-10-02 v1.1.27] prova di altri motori (Vulkan <-> ROCm) e esito di «Salva e applica»
  const [altBuilds, setAltBuilds] = useState(0)
  const [tryEngines, setTryEngines] = useState(true)
  const [applyMsg, setApplyMsg] = useState('')
  const pollRef = useRef(null)

  function resumePoll(jobId) {
    clearInterval(pollRef.current)
    setState('running')
    pollRef.current = setInterval(async () => {
      const pr = await fetch(`/api/tune/status/${jobId}`)
      const job = await pr.json()
      setLogs(job.logs || [])
      if (job.status === 'success' || job.status === 'failed') {
        clearInterval(pollRef.current)
        setResults(job.results || [])
        setBest(job.best || null)
        setBaseline(job.baseline || null)
        setState(job.status)
        if (job.error) setError(job.error)
      }
    }, 2500)
  }

  useEffect(() => {
    if (!targetId) return
    setState('idle'); setResults([]); setLogs([]); setError('')
    setBest(null); setBaseline(null)
    // [2026-10-01 v1.1.11] elenco da /api/deploy/models (percorsi relativi, anche sottocartelle) come nel Deploy;
    // prima /api/store/downloaded restituiva solo il nome file e i modelli nelle sottocartelle non si avviavano.
    fetch(`/api/deploy/models?target_id=${targetId}`)
      .then(r => r.json())
      .then(d => {
        const list = d.models || []
        setModels(list)
        // Il modello scelto nel Deploy compare da solo qui (lib/lastModel)
        const last = readLastModel(targetId)
        setSelected(list.includes(last) ? last : (list[0] || ''))
      })
    // Dopo refresh/rientro nella pagina ripristina il task di tuning in esecuzione
    fetch(`/api/tune/active?target_id=${targetId}`)
      .then(r => r.json())
      .then(d => {
        const jobs = d.jobs || []
        if (jobs.length > 0) {
          const j = jobs[0]
          if (j.model) setSelected(j.model)
          if (j.ctx_size) setCtxSize(j.ctx_size)
          if (j.goal) setGoal(j.goal)
          setLogs(j.last_logs || [])
          resumePoll(j.job_id)
        } else {
          // [2026-10-02 v1.1.23] Nessun tuning in corso: se l'ultimo e' concluso se ne mostrano esito e risultati
          // (prima, rimontando il pannello a fine tuning, tornava vuoto e non si capiva com'era andata).
          fetch(`/api/tune/last?target_id=${targetId}`).then(r => r.json()).then(x => {
            const job = x.job
            if (!job || (job.status !== 'success' && job.status !== 'failed')) return
            if (job.model) setSelected(job.model)
            if (job.ctx_size) setCtxSize(job.ctx_size)
            if (job.goal) setGoal(job.goal)
            setLogs(job.logs || [])
            setResults(job.results || [])
            setBest(job.best || null)
            setBaseline(job.baseline || null)
            setState(job.status)
            if (job.error) setError(job.error)
          }).catch(() => {})
        }
      })
      .catch(() => {})
    return () => clearInterval(pollRef.current)
  }, [targetId])

  // numero di build GPU installate (rocm/vulkan/cuda): se >1 compare l'opzione «prova anche gli altri motori»
  useEffect(() => {
    if (!targetId) return
    fetch(`/api/target/${targetId}/engines-installed`).then(r => r.json())
      .then(d => setAltBuilds((d.builds || []).filter(b => /rocm|vulkan|cuda/i.test(b.backend || '')).length))
      .catch(() => {})
  }, [targetId])

  // Carica la baseline proposta (parametri del Deploy senza MTP non supportato) ogni volta che cambia il modello
  useEffect(() => {
    if (!targetId || !selected) return
    setBaselineInfo(null)
    fetch(`/api/tune/baseline?target_id=${targetId}&model=${encodeURIComponent(selected)}`)
      .then(r => r.json())
      .then(d => {
        if (d.ok) { setBaselineArgs(d.args || ''); setBaselineInfo({ source: d.source, mtp: d.mtp }) }
      })
      .catch(() => {})
  }, [targetId, selected])

  async function start() {
    setState('running')
    setLogs([]); setResults([]); setError(''); setBest(null); setBaseline(null); setApplyMsg(''); setSaved(false)
    const res = await fetch('/api/tune/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_id: targetId,
        model: selected,
        ctx_size: ctxSize,
        goal,
        // Versione precedente: baseline_cfg: useBaseline ? DEFAULT_BASELINE : null,
        baseline_args: baselineMode === 'deploy' ? baselineArgs : (baselineMode === 'engine' ? '' : null),
        try_engines: tryEngines && altBuilds > 1,
      }),
    })
    const d = await res.json()
    if (!d.ok) { setState('failed'); setError(d.message || t('tune.startFail')); return }
    resumePoll(d.job_id)
  }

  // [2026-10-02 v1.1.27] «Salva e applica» ora SALVA, cambia motore se consigliato e RIAVVIA il modello con la configurazione
  // consigliata (il tuning lascia il server fermo). Versione precedente: POST /api/tune/save (solo salvataggio, motore spento).
  async function saveBest() {
    if (!best?.config) return
    setSaving(true); setApplyMsg('')
    try {
      const res = await fetch('/api/tune/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_id: targetId,
          model: selected,
          ctx_size: ctxSize,
          params: best.config,
          // t/s di decodifica (il Deploy li mostra come «misurati t/s»)
          score: best.metrics?.decode || 0,
          engine_path: best.engine?.path || null,
          engine_backend: best.engine?.backend || null,
          restart: true,
        }),
      })
      const d = await res.json()
      if (d.ok) {
        setSaved(true)
        setApplyMsg(t(d.started ? 'tune.appliedStarted' : 'tune.appliedSaved') + (d.switched ? ` — ${t('tune.engineChanged')}: ${d.switched}` : ''))
      } else setError(d.message || t('tune.saveFail'))
    } catch (e) {
      setError(t('tune.saveReqFail'))
    } finally {
      setSaving(false)
    }
  }

  let gain = null
  if (best && baseline && baseline.metrics?.decode > 0 && best.metrics?.decode > 0) {
    gain = ((best.metrics.decode - baseline.metrics.decode) / baseline.metrics.decode * 100).toFixed(1)
  }

  return (
    <div>
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 mb-6">
      <div className="bg-card rounded-xl p-6 border border-gray/30 lg:col-span-3">
        <p className="text-gray text-sm mb-4">
          {t('tune.autoDesc')}
        </p>

        <label className="block text-gray text-sm mb-2">{t('tune.selectModel')}</label>
        <select value={selected} onChange={e => { setSelected(e.target.value); writeLastModel(targetId, e.target.value) }}
          className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 mb-4 text-fg">
          {models.length === 0 && <option value="">{t('tune.emptyDir')}</option>}
          {models.map(m => <option key={m} value={m}>{m}</option>)}
        </select>

        <label className="block text-gray text-sm mb-2">{t('tune.ctxLabel')}</label>
        <CtxPicker value={ctxSize} onChange={setCtxSize} />

        <label className="block text-gray text-sm mb-2">{t('tune.goalLabel')}</label>
        <div className="grid grid-cols-2 gap-3 mb-4">
          {GOALS.map(v => (
            <button key={v} onClick={() => setGoal(v)}
              className={`p-3 rounded-lg border text-left transition ${goal === v ? 'border-blue bg-blue/20' : 'border-gray/40 hover:bg-gray/10'}`}>
              <div className={`font-semibold ${goal === v ? 'text-blue' : 'text-fg'}`}>{t(`tune.goal.${v}`)}</div>
              <div className="text-xs text-gray mt-1">{t(`tune.goal.${v}.hint`)}</div>
            </button>
          ))}
        </div>

        {/* [2026-10-01 v1.1.11] Baseline: parametri del Deploy (modificabili) / predefiniti del motore / nessuna */}
        <label className="block text-gray text-sm mb-2">{t('tune.baselineLabel')}</label>
        <div className="flex gap-4 text-sm text-gray mb-2">
          {[['deploy', t('tune.baselineMode.deploy')], ['engine', t('tune.baselineMode.engine')], ['none', t('tune.baselineMode.none')]].map(([k, label]) => (
            <label key={k} className="flex items-center gap-1.5 cursor-pointer">
              <input type="radio" name="baseline-mode" checked={baselineMode === k}
                onChange={() => setBaselineMode(k)} className="accent-green" />
              {label}
            </label>
          ))}
        </div>
        {baselineMode === 'deploy' && (
          <div className="mb-4">
            <textarea value={baselineArgs} onChange={e => setBaselineArgs(e.target.value)} rows={3}
              placeholder={t('tune.baselineEmpty')}
              className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-fg font-mono text-xs" />
            {baselineInfo && (
              <div className="text-xs text-gray mt-1">
                {t('deploy.sourceLabel')} {t(`deploy.source.${baselineInfo.source}`) === `deploy.source.${baselineInfo.source}` ? baselineInfo.source : t(`deploy.source.${baselineInfo.source}`)}
              </div>
            )}
          </div>
        )}
        {baselineInfo && !baselineInfo.mtp?.allowed && (
          <div className="mb-4 text-xs text-yellow inline-flex items-start gap-1.5">
            <IconAlert size={13} />
            <span>{t('tune.mtpOff')}</span>
          </div>
        )}
        <div className="mb-6" />

        {/* [2026-10-02 v1.1.27] chiarimento: il tuning prende il controllo del server */}
        <div className="mb-3 text-xs text-yellow bg-yellow/10 border border-yellow/30 rounded-lg p-3">{t('tune.takeover')}</div>
        {altBuilds > 1 && (
          <label className="flex items-start gap-2 mb-3 text-sm cursor-pointer">
            <input type="checkbox" className="mt-1" checked={tryEngines} onChange={e => setTryEngines(e.target.checked)} />
            <span><b>{t('tune.tryEngines')}</b><span className="block text-xs text-gray">{t('tune.tryEnginesHint')}</span></span>
          </label>
        )}
        <button onClick={start} disabled={state === 'running' || !selected}
          className="w-full bg-green text-bg font-bold py-2.5 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition">
          {state === 'running' ? t('tune.running') : t('tune.startAuto')}
        </button>
        {error && <div className="mt-3 text-sm text-red inline-flex items-center gap-1.5"><IconAlert size={14} />{error}</div>}
      </div>

      <div className="lg:col-span-2">
        <ProgressPanel title={t('tune.progress')} logs={logs} running={state === 'running'}
          emptyHint={t('tune.progressHint')} />
      </div>
      </div>

      {best && (
        <div className="bg-card rounded-xl p-5 border border-green/40 mb-6">
          <div className="text-sm font-semibold text-green mb-3 inline-flex items-center gap-1.5"><IconStar size={15} />{t('tune.recommended')}</div>
          <div className="font-mono text-xs text-fg mb-2">{best.label}</div>
          <div className="text-xs text-gray">
            {t('tune.metricsLine', { decode: best.metrics?.decode, prefill: best.metrics?.prefill, ttft: best.metrics?.ttft_ms, gpu: best.metrics?.gpu_util })}
          </div>
          <BeforeAfter baseline={baseline} best={best} />
          {gain != null && baseline && (
            <div className={`mt-3 text-sm font-semibold ${Number(gain) >= 0 ? 'text-green' : 'text-red'}`}>
              {Number(gain) >= 0
                ? t('tune.gainUp', { base: baseline.metrics?.decode, pct: Math.abs(Number(gain)) })
                : t('tune.gainDown', { base: baseline.metrics?.decode, pct: Math.abs(Number(gain)) })}
            </div>
          )}
          <button onClick={saveBest} disabled={saving || saved}
            className={`mt-4 w-full py-2 rounded-lg text-sm font-semibold transition ${saved ? 'bg-green/20 text-green cursor-default' : 'bg-green text-bg hover:opacity-90'}`}>
            {saved ? t('tune.saved') : (saving ? t('tune.saving') : t('tune.saveApply'))}
          </button>
          {saved && <div className="mt-2 text-xs text-gray">{applyMsg || t('tune.savedHint', { ctx: ctxSize })}</div>}
        </div>
      )}

      {results.length > 0 && (
        <div className="bg-card rounded-xl p-4 border border-gray/30">
          <div className="text-sm font-semibold mb-3">{t('tune.allRecords')}</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray border-b border-gray/30">
                <th className="text-left py-2 px-2">{t('tune.col.config')}</th>
                <th className="text-right py-2 px-2">{t('tune.col.decode')}</th>
                <th className="text-right py-2 px-2">{t('tune.col.prefill')}</th>
                <th className="text-right py-2 px-2">{t('tune.col.ttft')}</th>
                <th className="text-right py-2 px-2">GPU%</th>
                <th className="text-center py-2 px-2">{t('tune.col.recommended')}</th>
              </tr>
            </thead>
            <tbody>
              {[...results].sort((a, b) => (b.score || 0) - (a.score || 0)).map((r, i) => (
                <tr key={i} className={`border-b border-gray/20 ${r.recommended ? 'bg-green/10' : ''}`}>
                  <td className="py-2 px-2 font-mono">{r.label}</td>
                  <td className="py-2 px-2 text-right text-green">{r.metrics?.decode}</td>
                  <td className="py-2 px-2 text-right">{r.metrics?.prefill}</td>
                  <td className="py-2 px-2 text-right">{r.metrics?.ttft_ms}</td>
                  <td className="py-2 px-2 text-right">{r.metrics?.gpu_util}</td>
                  <td className="py-2 px-2 text-center">{r.recommended && <span className="text-green inline-flex justify-center"><IconStar size={14} /></span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ==================== Tuning AI ====================

function AITune({ targetId }) {
  const { t } = useI18n()
  const [models, setModels] = useState([])
  const [selected, setSelected] = useState('')
  const [ctxSize, setCtxSize] = useState(8192)
  const [goal, setGoal] = useState('latency')
  const [userDesc, setUserDesc] = useState('')

  // Configurazione
  const [cfg, setCfg] = useState({ api_url: '', api_key: '', model_name: '' })
  const [cfgSaved, setCfgSaved] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [testing, setTesting] = useState(false)
  const [showConfig, setShowConfig] = useState(false)

  // Task
  const [state, setState] = useState('idle')
  const [logs, setLogs] = useState([])
  const [rounds, setRounds] = useState([])
  const [best, setBest] = useState(null)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const pollRef = useRef(null)

  async function saveBest() {
    if (!best?.params) return
    setSaving(true)
    try {
      const res = await fetch('/api/ai-tune/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_id: targetId,
          model: selected,
          ctx_size: ctxSize,
          params: best.params,
          score: 0,
        }),
      })
      const d = await res.json()
      if (d.ok) setSaved(true)
      else setError(d.message || t('tune.saveFail'))
    } catch (e) {
      setError(t('tune.saveReqFail'))
    } finally {
      setSaving(false)
    }
  }

  function resumePoll(jobId) {
    clearInterval(pollRef.current)
    setState('running')
    pollRef.current = setInterval(async () => {
      const pr = await fetch(`/api/ai-tune/status/${jobId}`)
      const job = await pr.json()
      setLogs(job.logs || [])
      setRounds(job.rounds || [])
      if (job.status === 'success' || job.status === 'failed') {
        clearInterval(pollRef.current)
        setBest(job.best || null)
        setState(job.status)
        if (job.error) setError(job.error)
      }
    }, 3000)
  }

  useEffect(() => {
    if (!targetId) return
    // Recupera in parallelo elenco modelli e stato di esecuzione: se un modello e' in esecuzione (status.model),
    // lo seleziona in modo fisso, dopo un refresh della pagina non torna piu' al default.
    Promise.all([
      fetch(`/api/deploy/models?target_id=${targetId}`).then(r => r.json()),
      fetch(`/api/deploy/status?target_id=${targetId}`).then(r => r.json()),
    ]).then(([d, st]) => {
      const list = d.models || []
      setModels(list)
      const last = readLastModel(targetId)
      if (list.includes(last)) {
        setSelected(last)
      } else if (st.running && st.model && list.includes(st.model)) {
        setSelected(st.model)
      } else {
        setSelected(list[0] || '')
      }
    })
    // Carica la configurazione esistente
    fetch('/api/ai-tune/config').then(r => r.json()).then(d => {
      if (d.api_url) { setCfg(d); setCfgSaved(true) }
    })
    // Dopo refresh/rientro nella pagina ripristina il task di tuning AI in esecuzione
    fetch(`/api/ai-tune/active?target_id=${targetId}`)
      .then(r => r.json())
      .then(d => {
        const jobs = d.jobs || []
        if (jobs.length > 0) {
          const j = jobs[0]
          if (j.model) setSelected(j.model)
          if (j.ctx_size) setCtxSize(j.ctx_size)
          if (j.goal) setGoal(j.goal)
          setLogs(j.last_logs || [])
          resumePoll(j.job_id)
        }
      })
      .catch(() => {})
    return () => clearInterval(pollRef.current)
  }, [targetId])

  async function saveConfig() {
    await fetch('/api/ai-tune/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    })
    setCfgSaved(true)
    setShowConfig(false)
  }

  async function testConn() {
    setTesting(true); setTestResult(null)
    const res = await fetch('/api/ai-tune/test-connection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    })
    const d = await res.json()
    setTestResult(d)
    setTesting(false)
  }

  async function start() {
    setState('running'); setLogs([]); setRounds([]); setBest(null); setError('')
    const res = await fetch('/api/ai-tune/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_id: targetId, model: selected,
        ctx_size: ctxSize, goal, user_desc: userDesc,
      }),
    })
    const d = await res.json()
    if (!d.ok) { setState('failed'); setError(d.message || t('tune.startFail')); return }
    resumePoll(d.job_id)
  }

  return (
    <div>
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 mb-6">
      {/* Area di configurazione */}
      <div className="lg:col-span-3 space-y-6">
      <div className="bg-card rounded-xl p-6 border border-gray/30">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-bold">{t('tune.aiConfig')}</h2>
          <button onClick={() => setShowConfig(!showConfig)}
            className="text-sm text-blue hover:underline">
            {showConfig ? t('tune.collapse') : (cfgSaved ? t('tune.editConfig') : t('tune.configureApi'))}
          </button>
        </div>

        {cfgSaved && !showConfig && (
          <div className="text-sm text-green mb-2 inline-flex items-center gap-1.5"><IconCheck size={14} />{t('tune.configured', { url: cfg.api_url, model: cfg.model_name })}</div>
        )}

        {(showConfig || !cfgSaved) && (
          <div className="space-y-3">
            <div>
              <label className="block text-gray text-xs mb-1">{t('tune.apiUrl')}</label>
              <input value={cfg.api_url} onChange={e => setCfg({ ...cfg, api_url: e.target.value })}
                placeholder="http://192.168.50.223:8989/v1 or https://api.deepseek.com/v1"
                className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-sm text-fg" />
            </div>
            <div>
              <label className="block text-gray text-xs mb-1">{t('tune.apiKey')}</label>
              <input value={cfg.api_key} onChange={e => setCfg({ ...cfg, api_key: e.target.value })}
                placeholder="sk-..." type="password"
                className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-sm text-fg" />
            </div>
            <div>
              <label className="block text-gray text-xs mb-1">{t('tune.modelName')}</label>
              <input value={cfg.model_name} onChange={e => setCfg({ ...cfg, model_name: e.target.value })}
                placeholder="qwen3-27b / deepseek-chat"
                className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-sm text-fg" />
            </div>
            <div className="flex gap-3">
              <button onClick={testConn} disabled={testing || !cfg.api_url}
                className="px-4 py-2 text-sm bg-blue/20 text-blue rounded-lg disabled:opacity-40 hover:bg-blue/30 transition">
                {testing ? t('tune.testing') : t('tune.testConn')}
              </button>
              <button onClick={saveConfig} disabled={!cfg.api_url}
                className="px-4 py-2 text-sm bg-green text-bg rounded-lg disabled:opacity-40 hover:opacity-90 transition">
                {t('tune.saveConfig')}
              </button>
            </div>
            {testResult && (
              <div className={`text-sm inline-flex items-center gap-1.5 ${testResult.ok ? 'text-green' : 'text-red'}`}>
                {testResult.ok ? <IconCheck size={14} /> : <IconX size={14} />}{testResult.message}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Parametri di tuning */}
      <div className="bg-card rounded-xl p-6 border border-gray/30">
        <label className="block text-gray text-sm mb-2">{t('tune.selectModel')}</label>
        <select value={selected} onChange={e => { setSelected(e.target.value); writeLastModel(targetId, e.target.value) }}
          className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 mb-4 text-fg">
          {models.length === 0 && <option value="">{t('tune.emptyDir')}</option>}
          {models.map(m => <option key={m} value={m}>{m}</option>)}
        </select>

        <label className="block text-gray text-sm mb-2">{t('tune.ctxLabel')}</label>
        <CtxPicker value={ctxSize} onChange={setCtxSize} />

        <label className="block text-gray text-sm mb-2">{t('tune.goalLabel')}</label>
        <div className="grid grid-cols-3 gap-3 mb-4">
          {GOALS.map(v => (
            <button key={v} onClick={() => setGoal(v)}
              className={`p-3 rounded-lg border text-left transition ${goal === v ? 'border-blue bg-blue/20' : 'border-gray/40 hover:bg-gray/10'}`}>
              <div className={`font-semibold ${goal === v ? 'text-blue' : 'text-fg'}`}>{t(`tune.goal.${v}`)}</div>
              <div className="text-xs text-gray mt-1">{t(`tune.goal.${v}.hint`)}</div>
            </button>
          ))}
        </div>

        <label className="block text-gray text-sm mb-2">{t('tune.descLabel')}</label>
        <textarea value={userDesc} onChange={e => setUserDesc(e.target.value)}
          placeholder={t('tune.descPlaceholder')}
          rows={2}
          className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-sm text-fg mb-4 resize-none" />

        <button onClick={start} disabled={state === 'running' || !selected || !cfgSaved}
          className="w-full bg-purple text-white font-bold py-2.5 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition">
          {state === 'running' ? t('tune.aiRunning') : t('tune.startAi')}
        </button>
        {!cfgSaved && <div className="mt-2 text-xs text-gray">{t('tune.needCfg')}</div>}
        {error && <div className="mt-3 text-sm text-red inline-flex items-center gap-1.5"><IconAlert size={14} />{error}</div>}
      </div>
      </div>

      {/* Colonna destra: avanzamento */}
      <div className="lg:col-span-2">
        <ProgressPanel title={t('tune.aiProgress')} logs={logs} running={state === 'running'}
          emptyHint={t('tune.aiProgressHint')} />
      </div>
      </div>

      {/* Risultati di ogni round */}
      {rounds.length > 0 && (
        <div className="bg-card rounded-xl p-4 border border-gray/30 mb-6">
          <div className="text-sm font-semibold mb-3">{t('tune.rounds', { n: rounds.length })}</div>
          <div className="space-y-3">
            {rounds.map((r, i) => (
              <div key={i} className="bg-bg rounded-lg p-3 text-xs">
                <div className="text-gray mb-1">{t('tune.round', { n: r.round })} · {r.reasoning?.slice(0, 100)}</div>
                {r.metrics ? (
                  <div className="flex gap-4 text-fg/80">
                    <span>{t('tune.decode')} <b className="text-green">{r.metrics.decode}</b> t/s</span>
                    <span>{t('tune.prefill')} {r.metrics.prefill} t/s</span>
                    <span>GPU {r.metrics.gpu_util}%</span>
                    <span>{t('tune.vram')} {r.metrics.gpu_mem_pct}%</span>
                  </div>
                ) : (
                  <div className="text-red">{t('tune.testFail')}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Raccomandazione finale */}
      {best && (
        <div className="bg-card rounded-xl p-5 border border-purple/40">
          <div className="text-sm font-semibold text-purple mb-3 inline-flex items-center gap-1.5">
            <IconBot size={15} />{t('tune.finalRec', { n: best.round, conf: best.confidence })}
          </div>
          <div className="bg-bg rounded-lg p-3 font-mono text-xs text-fg mb-3 whitespace-pre-wrap">
            {Object.entries(best.params || {}).map(([k, v]) => (
              <div key={k}>--{k}{v ? ` ${v}` : ''}</div>
            ))}
          </div>
          <div className="text-xs text-gray">{best.reasoning}</div>
          <button onClick={saveBest} disabled={saving || saved}
            className={`mt-4 w-full py-2 rounded-lg text-sm font-semibold transition ${saved ? 'bg-green/20 text-green cursor-default' : 'bg-green text-bg hover:opacity-90'}`}>
            {saved ? t('tune.saved') : (saving ? t('tune.saving') : t('tune.saveApply'))}
          </button>
          {saved && <div className="mt-2 text-xs text-gray">{t('tune.savedHint', { ctx: ctxSize })}</div>}
        </div>
      )}
    </div>
  )
}

// ==================== Componente principale ====================

// [2026-10-01 v1.1.9] embedded=true: usato dentro la pagina Monitoraggio (titolo di sezione al posto del titolo di pagina)
export default function Tune({ targetId, embedded = false }) {
  const { t } = useI18n()
  const [tab, setTab] = useState('auto') // auto | ai

  return (
    <div>
      {embedded
        ? null
        : <h1 className="text-2xl font-bold mb-6">{t('tune.title')}</h1>}

      {/* Cambio scheda */}
      <div className="flex gap-1 mb-6 bg-card rounded-lg p-1 border border-gray/30 max-w-xs">
        <button onClick={() => setTab('auto')}
          className={`flex-1 py-2 px-4 rounded-md text-sm font-semibold transition ${tab === 'auto' ? 'bg-green text-bg' : 'text-gray hover:text-fg'}`}>
          {t('tune.auto')}
        </button>
        <button onClick={() => setTab('ai')}
          className={`flex-1 py-2 px-4 rounded-md text-sm font-semibold transition ${tab === 'ai' ? 'bg-purple text-white' : 'text-gray hover:text-fg'}`}>
          {t('tune.ai')}
        </button>
      </div>

      <p className="text-xs text-gray -mt-2 mb-4">{t('tune.stopsServer')}</p>

      {tab === 'auto' ? <AutoTune targetId={targetId} /> : <AITune targetId={targetId} />}
    </div>
  )
}
