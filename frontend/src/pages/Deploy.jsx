import { useState, useEffect, useRef } from 'react'
import { IconRefresh, IconPlay, IconStop, IconRocket } from '../components/Icons'
import LongVideoDeploy from './LongVideoDeploy'
import { useI18n } from '../i18n/I18nContext'

export default function Deploy({ targetId, target }) {
  const { t } = useI18n()
  const isVideo = target?.engine_type === 'comfyui'
  const [videoMode, setVideoMode] = useState('short') // short | long
  if (!isVideo) return <TextDeploy targetId={targetId} target={target} />
  return (
    <div>
      <div className="inline-flex gap-1 p-1 rounded-lg bg-card border border-gray/30 mb-6">
        {[['short', t('deploy.shortVideo')], ['long', t('deploy.longVideo')]].map(([k, label]) => (
          <button key={k} onClick={() => setVideoMode(k)}
            className={`px-4 py-1.5 rounded-md text-sm transition ${
              videoMode === k ? 'bg-blue text-bg font-semibold' : 'text-gray hover:text-fg'}`}>
            {label}
          </button>
        ))}
      </div>
      {videoMode === 'short'
        ? <VideoDeploy targetId={targetId} target={target} />
        : <LongVideoDeploy targetId={targetId} target={target} />}
    </div>
  )
}

/* Ricorda l'ultimo modello selezionato su ogni macchina target: alla riapertura della pagina lo ripristina, invece di cadere ogni volta sul primo della lista */
const LAST_MODEL_KEY = 'readyllm:lastModel'

function readLastModel(targetId) {
  try {
    const m = JSON.parse(localStorage.getItem(LAST_MODEL_KEY) || '{}')
    return m[targetId] || ''
  } catch { return '' }
}

function writeLastModel(targetId, model) {
  try {
    const m = JSON.parse(localStorage.getItem(LAST_MODEL_KEY) || '{}')
    m[targetId] = model
    localStorage.setItem(LAST_MODEL_KEY, JSON.stringify(m))
  } catch { /* errori di scrittura (es. modalita' privata) ignorabili */ }
}

/* ==================== Deploy dei modelli testuali (llama.cpp / vLLM) ==================== */

function TextDeploy({ targetId }) {
  const { t } = useI18n()
  const [models, setModels] = useState([])
  const [selected, setSelected] = useState('')
  const [status, setStatus] = useState(null)
  const [msg, setMsg] = useState('')
  const [argsText, setArgsText] = useState('')
  const [argsMeta, setArgsMeta] = useState(null) // {source, score, ts, reasoning}
  const [loadingArgs, setLoadingArgs] = useState(false)

  useEffect(() => {
    if (!targetId) return
    setMsg('')
    // Recupera in parallelo elenco modelli e stato di esecuzione, e solo dopo averli ottenuti entrambi decide la selezione:
    // se un modello e' in esecuzione (status.model), lo seleziona in modo fisso, dopo un refresh della pagina non torna piu' al default.
    Promise.all([
      fetch(`/api/deploy/models?target_id=${targetId}`).then(r => r.json()),
      fetch(`/api/deploy/status?target_id=${targetId}`).then(r => r.json()),
    ]).then(([md, st]) => {
      const list = md.models || []
      setModels(list)
      setStatus(st)
      if (md.error) setMsg(md.error)
      // Priorita' di selezione: modello in esecuzione > ultimo selezionato su questa macchina (ancora in elenco) > primo della lista
      const restore = (cur) => {
        if (cur) return cur
        const last = readLastModel(targetId)
        return (last && list.includes(last)) ? last : (list[0] || '')
      }
      if (st.running && st.model && list.includes(st.model)) {
        setSelected(st.model)
      } else {
        // Con il doppio avvio di StrictMode la seconda sonda SSH puo' dare occasionalmente running=false:
        // il valore si assegna con la priorita' di ripristino solo quando non c'e' ancora una selezione, non si ribalta mai una selezione gia' decisa
        setSelected(restore)
      }
    }).catch(() => {
      // Il fallimento della sonda status (instabilita' SSH ecc.) non significa che non sia in esecuzione: si recupera solo l'elenco modelli,
      // mantenendo la selezione esistente, e solo senza selezione si ripristina l'ultima / si cade sul primo della lista
      fetch(`/api/deploy/models?target_id=${targetId}`)
        .then(r => r.json())
        .then(d => {
          const list = d.models || []
          setModels(list)
          const last = readLastModel(targetId)
          setSelected((cur) => cur || (last && list.includes(last) ? last : list[0]) || '')
        })
        .catch(() => {})
    })
  }, [targetId])

  // Ricorda l'ultimo modello selezionato su ogni macchina target, ripristinato alla riapertura della pagina
  useEffect(() => {
    if (targetId && selected) writeLastModel(targetId, selected)
  }, [targetId, selected])

  // Dopo la scelta del modello recupera i parametri di default: priorita' all'ultimo tuning, ripiego sulla generazione deterministica
  useEffect(() => {
    if (!targetId || !selected) {
      setArgsText('')
      setArgsMeta(null)
      return
    }
    // Protezione dalle risposte obsolete: i parametri di tuning presenti nel registro locale rispondono subito, se mancano serve rilevare l'hardware via SSH (circa 4s),
    // dopo un cambio di modello la vecchia richiesta lenta non deve sovrascrivere la risposta veloce del nuovo modello (vince l'ultima arrivata -> vince la piu' recente)
    let ignore = false
    setLoadingArgs(true)
    fetch(`/api/deploy/default-args?target_id=${targetId}&model=${encodeURIComponent(selected)}`)
      .then(r => r.json())
      .then(d => {
        if (ignore) return
        setArgsText(d.args || '')
        setArgsMeta(d)
      })
      .catch(() => { if (!ignore) setArgsMeta(null) })
      .finally(() => { if (!ignore) setLoadingArgs(false) })
    return () => { ignore = true }
  }, [targetId, selected])

  // Poll status until `running` flips to the expected value (or timeout).
  // Backend /start only sends the launch command without waiting for the engine
  // to be ready, so a single status query often returns false before the process
  // is detected, leaving the buttons in a stale state.
  async function pollStatus(expectRunning, timeoutMs = 90000, intervalMs = 1500) {
    const t0 = Date.now()
    while (true) {
      const s = await fetch(`/api/deploy/status?target_id=${targetId}`).then(r => r.json())
      setStatus(s)
      if (!!s.running === expectRunning) return
      if (Date.now() - t0 > timeoutMs) return
      await new Promise(r => setTimeout(r, intervalMs))
    }
  }

  async function start() {
    setMsg(t('deploy.starting'))
    const res = await fetch('/api/deploy/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_id: targetId, model: selected, args_text: argsText }),
    })
    const d = await res.json()
    setMsg(d.message)
    if (d.success) await pollStatus(true)
  }

  async function stop() {
    setMsg(t('deploy.stopping'))
    const res = await fetch(`/api/deploy/stop?target_id=${targetId}`, { method: 'POST' })
    const d = await res.json()
    setMsg(d.message)
    if (d.success) await pollStatus(false)
  }

  function resetArgs() {
    if (!targetId || !selected) return
    setLoadingArgs(true)
    fetch(`/api/deploy/default-args?target_id=${targetId}&model=${encodeURIComponent(selected)}`)
      .then(r => r.json())
      .then(d => { setArgsText(d.args || ''); setArgsMeta(d) })
      .finally(() => setLoadingArgs(false))
  }

  const sourceLabel = {
    tuner: t('deploy.source.tuner'),
    ai_tuner: t('deploy.source.ai_tuner'),
    generated: t('deploy.source.generated'),
    default: t('deploy.source.default'),
  }[argsMeta?.source] || ''

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">{t('deploy.title')}</h1>

      <div className="bg-card rounded-xl p-6 border border-gray/30 max-w-2xl">
        <div className="flex items-center gap-3 mb-6">
          <span className={`w-3 h-3 rounded-full ${status?.running ? 'bg-green' : 'bg-gray'}`} />
          <span className="font-semibold">{status?.running ? t('deploy.running') : t('deploy.stopped')}</span>
          {status?.engine && (
            <span className="text-xs px-2 py-0.5 rounded bg-blue/15 text-blue">
              {t('deploy.engine')} {status.engine === 'vllm' ? 'vLLM' : 'llama.cpp'}
            </span>
          )}
        </div>

        <label className="block text-gray text-sm mb-2">{t('deploy.selectModel')}</label>
        <select
          value={selected}
          onChange={e => setSelected(e.target.value)}
          className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 mb-2 text-fg"
        >
          {models.length === 0 && <option value="">{t('deploy.emptyDir')}</option>}
          {models.map(m => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        <div className="text-xs text-gray/70 mb-6">{t('deploy.modelCount', { n: models.length })}</div>

        <div className="flex items-center justify-between mb-2">
          <label className="block text-gray text-sm">{t('deploy.argsLabel')}</label>
          <button
            onClick={resetArgs}
            disabled={!selected || loadingArgs}
            className="text-xs text-blue hover:underline disabled:opacity-40"
          >
            {loadingArgs ? t('deploy.loadingArgs') : (<span className="inline-flex items-center gap-1"><IconRefresh size={13} />{t('deploy.resetDefault')}</span>)}
          </button>
        </div>
        {argsMeta && sourceLabel && (
          <div className="text-xs mb-2">
            <span className="text-gray">{t('deploy.sourceLabel')}</span>
            <span className={
              argsMeta.source === 'generated' || argsMeta.source === 'default'
                ? 'text-gray' : 'text-green'
            }>
              {sourceLabel}
            </span>
            {argsMeta.score > 0 && <span className="text-gray"> · {t('deploy.score', { score: argsMeta.score })}</span>}
            {argsMeta.ts && <span className="text-gray/60"> · {argsMeta.ts}</span>}
          </div>
        )}
        <textarea
          value={argsText}
          onChange={e => setArgsText(e.target.value)}
          spellCheck={false}
          placeholder="--ctx-size 8192 --n-gpu-layers all --batch-size 4096 ..."
          className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-fg font-mono text-xs h-24 focus:border-blue outline-none resize-y"
        />
        <div className="text-xs text-gray/60 mb-6">
          {t('deploy.argsHint')}
        </div>

        <div className="flex gap-3">
          <button
            onClick={start}
            disabled={status?.running || !selected}
            className="flex-1 bg-green text-bg font-bold py-2 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition"
          >
            <span className="inline-flex items-center justify-center gap-1.5"><IconPlay size={14} />{t('deploy.start')}</span>
          </button>
          <button
            onClick={stop}
            disabled={!status?.running}
            className="flex-1 bg-red text-bg font-bold py-2 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition"
          >
            <span className="inline-flex items-center justify-center gap-1.5"><IconStop size={13} />{t('deploy.stop')}</span>
          </button>
        </div>

        {msg && <div className="mt-4 text-sm text-gray">{msg}</div>}
      </div>
    </div>
  )
}

/* ==================== Deploy dei modelli video (ComfyUI) ==================== */

const RES_PRESETS = [
  { label: '480p (832×480)', width: 832, height: 480 },
  { label: '720p (1280×720)', width: 1280, height: 720 },
  { label: 'Portrait (480×832)', width: 480, height: 832 },
]

function VideoDeploy({ targetId, target }) {
  const { t } = useI18n()
  const [models, setModels] = useState([])
  const [selected, setSelected] = useState('')
  const [status, setStatus] = useState(null)
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState({
    prompt: '',
    negative_prompt: '',
    width: 832,
    height: 480,
    length: 49,
    steps: 30,
    cfg: 6.0,
    seed: '',
    fps: 16,
    enhance: true,
  })
  const [job, setJob] = useState(null) // {prompt_id, state, files}
  const pollRef = useRef(null)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  useEffect(() => {
    if (!targetId) return
    setMsg('')
    fetch(`/api/deploy/status?target_id=${targetId}`).then(r => r.json()).then(setStatus)
    // Scansiona la cartella diffusion_models di ComfyUI sulla macchina target ed elenca i modelli realmente scaricati
    fetch(`/api/deploy/video-models?target_id=${targetId}`)
      .then(r => r.json())
      .then(d => {
        const list = d.models || []
        setModels(list)
        // Ripristina l'ultimo selezionato (ancora in elenco) > primo della lista
        const last = readLastModel(targetId)
        setSelected((cur) => {
          if (cur) return cur
          if (last && list.some((m) => m.filename === last)) return last
          return list[0] ? list[0].filename : ''
        })
      })
  }, [targetId])

  // Ricorda l'ultimo modello selezionato su ogni macchina target, ripristinato alla riapertura della pagina
  useEffect(() => {
    if (targetId && selected) writeLastModel(targetId, selected)
  }, [targetId, selected])

  // Polling dell'avanzamento della generazione
  useEffect(() => {
    if (!job?.prompt_id || job.state === 'completed') return
    pollRef.current = setInterval(async () => {
      const d = await fetch(
        `/api/deploy/generate/progress?target_id=${targetId}&prompt_id=${job.prompt_id}`
      ).then(r => r.json())
      setJob(j => (j ? { ...j, ...d } : j))
      if (d.state === 'completed') clearInterval(pollRef.current)
    }, 3000)
    return () => clearInterval(pollRef.current)
  }, [job?.prompt_id])

  // Poll status until `running` flips to the expected value (or timeout).
  async function pollStatus(expectRunning, timeoutMs = 90000, intervalMs = 1500) {
    const t0 = Date.now()
    while (true) {
      const s = await fetch(`/api/deploy/status?target_id=${targetId}`).then(r => r.json())
      setStatus(s)
      if (!!s.running === expectRunning) return
      if (Date.now() - t0 > timeoutMs) return
      await new Promise(r => setTimeout(r, intervalMs))
    }
  }

  async function startEngine() {
    setMsg(t('deploy.startingComfyui'))
    const res = await fetch('/api/deploy/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_id: targetId, model: selected, args_text: '' }),
    })
    const d = await res.json()
    setMsg(d.message)
    if (d.success) await pollStatus(true)
  }

  async function stopEngine() {
    setMsg(t('deploy.stopping'))
    const res = await fetch(`/api/deploy/stop?target_id=${targetId}`, { method: 'POST' })
    const d = await res.json()
    setMsg(d.message)
    if (d.success) await pollStatus(false)
  }

  async function generate() {
    setMsg('')
    const res = await fetch('/api/deploy/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_id: targetId,
        prompt: form.prompt,
        model_name: selected,
        negative_prompt: form.negative_prompt,
        width: Number(form.width),
        height: Number(form.height),
        length: Number(form.length),
        steps: Number(form.steps),
        cfg: Number(form.cfg),
        seed: form.seed === '' ? null : Number(form.seed),
        fps: Number(form.fps),
        enhance: !!form.enhance,
      }),
    })
    const d = await res.json()
    if (!d.success) { setMsg(d.message || t('deploy.submitFail')); return }
    setJob({
      prompt_id: d.prompt_id,
      state: 'queued',
      enhanced: d.enhanced,
      final_prompt: d.final_prompt,
      reasoning: d.reasoning,
    })
  }

  const stateLabel = {
    queued: t('deploy.status.queued'),
    running: t('deploy.status.running'),
    completed: t('deploy.status.completed'),
    unknown: t('deploy.status.unknown'),
    error: t('deploy.status.error'),
  }[job?.state] || ''

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">{t('deploy.videoTitle')}</h1>

      <div className="bg-card rounded-xl p-6 border border-gray/30 max-w-2xl">
        <div className="flex items-center gap-3 mb-6">
          <span className={`w-3 h-3 rounded-full ${status?.running ? 'bg-green' : 'bg-gray'}`} />
          <span className="font-semibold">{status?.running ? t('deploy.comfyuiRunning') : t('deploy.comfyuiStopped')}</span>
          <span className="text-xs px-2 py-0.5 rounded bg-purple/15 text-purple">{t('deploy.engine')} ComfyUI</span>
        </div>

        {!status?.running ? (
          <div className="mb-6">
            <div className="text-sm text-gray mb-3">
              {t('deploy.comfyuiHint')}
            </div>
            <div className="flex gap-3">
              <button
                onClick={startEngine}
                className="flex-1 bg-green text-bg font-bold py-2 rounded-lg hover:opacity-90 transition"
              >
                <span className="inline-flex items-center justify-center gap-1.5"><IconPlay size={14} />{t('deploy.startComfyui')}</span>
              </button>
              <button
                onClick={stopEngine}
                disabled={!status?.running}
                className="flex-1 bg-red text-bg font-bold py-2 rounded-lg disabled:opacity-40 hover:opacity-90 transition"
              >
                <span className="inline-flex items-center justify-center gap-1.5"><IconStop size={13} />{t('deploy.stop')}</span>
              </button>
            </div>
            {msg && <div className="mt-3 text-sm text-gray">{msg}</div>}
          </div>
        ) : (
          <>
            <label className="block text-gray text-sm mb-2">{t('deploy.videoModel')}</label>
            <select
              value={selected}
              onChange={e => setSelected(e.target.value)}
              className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 mb-1 text-fg"
            >
              {models.length === 0 && <option value="">{t('deploy.noVideoModel')}</option>}
              {models.map(m => (
                <option key={m.filename} value={m.filename}>
                  {m.name}
                </option>
              ))}
            </select>
            <div className="text-xs text-gray/70 mb-5">{t('deploy.videoModelCount', { n: models.length })}</div>

            <label className="block text-gray text-sm mb-2">{t('deploy.promptLabel')}</label>
            <textarea
              value={form.prompt}
              onChange={e => set('prompt', e.target.value)}
              placeholder={t('deploy.promptPlaceholder')}
              className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-fg text-sm h-20 focus:border-blue outline-none resize-y mb-2"
            />

            {/* Interruttore di orchestrazione intelligente con AI */}
            <button
              type="button"
              onClick={() => set('enhance', !form.enhance)}
              className="flex items-center gap-2 mb-5 group"
            >
              <span className={`relative w-9 h-5 rounded-full transition-colors ${
                form.enhance ? 'bg-blue' : 'bg-gray/40'
              }`}>
                <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-bg transition-transform ${
                  form.enhance ? 'translate-x-4' : ''
                }`} />
              </span>
              <span className="text-sm text-fg">{t('deploy.aiEnhance')}</span>
              <span className="text-xs text-gray/70">
                {form.enhance ? t('deploy.enhanceOn') : t('deploy.enhanceOff')}
              </span>
            </button>

            <label className="block text-gray text-sm mb-2">{t('deploy.resolution')}</label>
            <div className="flex gap-2 mb-4">
              {RES_PRESETS.map(r => (
                <button
                  key={r.label}
                  onClick={() => { set('width', r.width); set('height', r.height) }}
                  className={`px-3 py-1.5 rounded-lg text-sm border transition ${
                    form.width === r.width && form.height === r.height
                      ? 'border-blue bg-blue/15 text-blue'
                      : 'border-gray/40 text-gray hover:border-gray'
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-3 gap-3 mb-4">
              <Field label={t('deploy.frames')} value={form.length} onChange={v => set('length', v)} hint={t('deploy.framesHint')} />
              <Field label={t('deploy.steps')} value={form.steps} onChange={v => set('steps', v)} hint={t('deploy.stepsHint')} />
              <Field label="CFG" value={form.cfg} onChange={v => set('cfg', v)} hint={t('deploy.cfg')} />
            </div>
            <div className="grid grid-cols-2 gap-3 mb-6">
              <Field label={t('deploy.fps')} value={form.fps} onChange={v => set('fps', v)} />
              <Field label={t('deploy.seed')} value={form.seed} onChange={v => set('seed', v)} placeholder={t('deploy.seedPlaceholder')} />
            </div>

            <button
              onClick={generate}
              disabled={!form.prompt || !selected}
              className="w-full bg-gradient-to-r from-blue to-purple text-bg font-bold py-2.5 rounded-lg disabled:opacity-40 hover:opacity-90 transition"
            >
              <span className="inline-flex items-center justify-center gap-1.5"><IconRocket size={15} />{t('deploy.generate')}</span>
            </button>
            {msg && <div className="mt-3 text-sm text-red">{msg}</div>}
          </>
        )}

        {/* Avanzamento della generazione */}
        {job && (
          <div className="mt-6 p-4 rounded-lg bg-bg border border-gray/30">
            <div className="flex items-center gap-2 mb-2">
              {job.state !== 'completed' && (
                <span className="w-2 h-2 rounded-full bg-blue animate-pulse" />
              )}
              <span className="text-sm font-semibold text-fg">{stateLabel}</span>
            </div>
            {job.enhanced && (
              <div className="mb-3 p-3 rounded-lg bg-blue/5 border border-blue/20">
                <div className="text-xs font-semibold text-blue mb-1">{t('deploy.aiPrompt')}</div>
                {job.final_prompt && (
                  <div className="text-xs text-fg/80 italic leading-relaxed mb-1">“{job.final_prompt}”</div>
                )}
                {job.reasoning && (
                  <div className="text-[11px] text-gray/70 leading-relaxed">{job.reasoning}</div>
                )}
              </div>
            )}
            {job.state === 'completed' && job.files?.length > 0 && (
              <div className="text-xs text-gray">
                {job.files.map((f, i) => {
                  const src = `/api/deploy/video-file?target_id=${targetId}`
                    + `&filename=${encodeURIComponent(f.filename)}`
                    + `&subfolder=${encodeURIComponent(f.subfolder || '')}`
                  return (
                    <div key={i} className="mb-3">
                      <video
                        src={src}
                        controls
                        autoPlay
                        loop
                        playsInline
                        className="w-full rounded-lg border border-gray/30 bg-black"
                        style={{ maxHeight: '420px' }}
                      />
                      <div className="mt-1 font-mono break-all text-gray/60">
                        {f.subfolder ? `${f.subfolder}/` : ''}{f.filename}
                      </div>
                    </div>
                  )
                })}
                <div className="mt-1 text-gray/60">{t('deploy.fileSaved')}</div>
              </div>
            )}
            {job.state === 'completed' && (!job.files || job.files.length === 0) && (
              <div className="text-xs text-gray">{t('deploy.noOutput')}</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ label, value, onChange, hint, placeholder }) {
  return (
    <div>
      <label className="block text-gray text-xs mb-1">{label}</label>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        className="w-full bg-bg border border-gray/40 rounded-lg px-2 py-1.5 text-fg text-sm focus:border-blue outline-none"
      />
      {hint && <div className="text-[10px] text-gray/50 mt-0.5">{hint}</div>}
    </div>
  )
}
