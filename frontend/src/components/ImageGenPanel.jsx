import { useEffect, useRef, useState } from 'react'
import { IconRocket, IconBox, IconPlay } from './Icons'
import { useI18n } from '../i18n/I18nContext'

/**
 * Pannello testo-immagine (ComfyUI) — v1.1.39, 2026-10-22.
 * Integrato dal progetto originale (htobty/ReadyLLM, commit a0416b7), commenti e messaggi in italiano.
 *
 * Volutamente con le stesse classi di stile e lo stesso layout di VideoDeploy: scheda bg-card, indicatore di stato, prompt +
 * interruttore di orchestrazione AI, preset di dimensione, parametri su tre colonne, pulsante di generazione sfumato, area di avanzamento.
 * L'unica differenza e' che il risultato e' un'anteprima <img> invece di <video>, e che non ci sono frame / fps.
 */

const IMG_PRESETS = [
  { label: '1024×1024', width: 1024, height: 1024 },
  { label: '832×1216', width: 832, height: 1216 },
  { label: '1216×832', width: 1216, height: 832 },
]

const LAST_IMG_KEY = 'readyllm:lastImageModel'

function readLastImageModel(targetId) {
  try {
    return JSON.parse(localStorage.getItem(LAST_IMG_KEY) || '{}')[targetId] || ''
  } catch { return '' }
}

function writeLastImageModel(targetId, model) {
  try {
    const m = JSON.parse(localStorage.getItem(LAST_IMG_KEY) || '{}')
    m[targetId] = model
    localStorage.setItem(LAST_IMG_KEY, JSON.stringify(m))
  } catch { /* in navigazione privata localStorage puo' non essere disponibile: si ignora */ }
}

export default function ImageGenPanel({ targetId, target }) {
  const { t } = useI18n()
  const [models, setModels] = useState([])
  const [selected, setSelected] = useState('')
  const [status, setStatus] = useState(null)
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState({
    prompt: '',
    negative_prompt: '',
    width: 1024,
    height: 1024,
    steps: 20,
    cfg: 2.5,
    seed: '',
    enhance: true,
  })
  const [job, setJob] = useState(null)
  const [busy, setBusy] = useState(false)
  const pollRef = useRef(null)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  useEffect(() => {
    if (!targetId) return
    setMsg('')
    fetch(`/api/deploy/status?target_id=${targetId}`, { cache: 'no-store' })
      .then(r => r.json()).then(setStatus)
      // un rilevamento fallito non significa servizio fermo: resta null, e solo uno stato «esplicitamente fermo» blocca la vista
      .catch(() => setStatus(null))
    fetch(`/api/deploy/image-models?target_id=${targetId}`, { cache: 'no-store' })
      .then(r => r.json())
      .then(d => {
        const list = Array.isArray(d.models) ? d.models : []
        setModels(list)
        // Se il backend ha modelli ma il frontend riceve un elenco vuoto, si mostra la risposta grezza: cosi' un errore silenzioso
        // come «0 modelli immagine» non nasconde la causa vera.
        if (!d.error && list.length === 0) {
          setMsg(`${t('deploy.imgEmptyList')} ${JSON.stringify(d).slice(0, 200)}`)
        } else if (d.error) setMsg(d.error)
        const last = readLastImageModel(targetId)
        setSelected(cur => {
          if (cur && list.some(m => m.filename === cur)) return cur
          if (last && list.some(m => m.filename === last)) return last
          return list[0] ? list[0].filename : ''
        })
      })
      .catch(e => setMsg(String(e)))
  }, [targetId])

  useEffect(() => {
    if (targetId && selected) writeLastImageModel(targetId, selected)
  }, [targetId, selected])

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

  async function generate() {
    if (busy) return
    setMsg('')
    setBusy(true)
    // Il loader della voce scelta decide la catena nel backend: checkpoint usa direttamente CheckpointLoaderSimple; unet (come
    // Qwen-Image) deve portare con se' anche CLIP/VAE abbinati, altrimenti il backend cerca il file in checkpoints come se fosse un
    // checkpoint e la validazione di ComfyUI respinge l'intero ordine.
    const m = models.find(x => x.filename === selected)
    try {
      const res = await fetch('/api/deploy/image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_id: targetId,
          prompt: form.prompt,
          model_name: selected,
          loader: m?.loader || 'checkpoint',
          clip_name: m?.clip_name || '',
          vae_name: m?.vae_name || '',
          negative_prompt: form.negative_prompt,
          width: Number(form.width),
          height: Number(form.height),
          steps: Number(form.steps),
          cfg: Number(form.cfg),
          seed: form.seed === '' ? null : Number(form.seed),
          enhance: !!form.enhance,
        }),
      })
      const d = await res.json().catch(() => ({}))
      if (!res.ok || !d.success) {
        setMsg(d.message || `${t('deploy.imgSubmitFail')} (HTTP ${res.status})`)
        return
      }
      setJob({
        prompt_id: d.prompt_id,
        state: 'queued',
        enhanced: d.enhanced,
        final_prompt: d.final_prompt,
        reasoning: d.reasoning,
      })
    } catch (e) {
      setMsg(`${t('deploy.imgSubmitFail')}: ${e.message || e}`)
    } finally {
      setBusy(false)
    }
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
      <h1 className="text-2xl font-bold mb-6">{t('deploy.imageTitle')}</h1>

      <div className="bg-card rounded-xl p-6 border border-gray/30 max-w-2xl">
        <div className="flex items-center gap-3 mb-6">
          <span className={`w-3 h-3 rounded-full ${status?.running ? 'bg-green' : 'bg-gray'}`} />
          <span className="font-semibold">{status?.running ? t('deploy.comfyuiRunning') : t('deploy.comfyuiStopped')}</span>
          <span className="text-xs px-2 py-0.5 rounded bg-purple/15 text-purple">{t('deploy.engine')} ComfyUI</span>
        </div>

        {status && status.running === false ? (
          <div className="text-sm text-gray">{t('deploy.comfyuiHint')}</div>
        ) : (
          <>
            <label className="block text-gray text-sm mb-2">{t('deploy.imageModel')}</label>
            <select
              value={selected}
              onChange={e => setSelected(e.target.value)}
              className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 mb-1 text-fg"
            >
              {models.length === 0 && <option value="">{t('deploy.noImageModel')}</option>}
              {models.map(m => (
                <option key={m.filename} value={m.filename}>{m.name}</option>
              ))}
            </select>
            <div className="text-xs text-gray/70 mb-5">{t('deploy.imageModelCount', { n: models.length })}</div>

            <label className="block text-gray text-sm mb-2">{t('deploy.promptLabel')}</label>
            <textarea
              value={form.prompt}
              onChange={e => set('prompt', e.target.value)}
              placeholder={t('deploy.imagePromptPlaceholder')}
              className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-fg text-sm h-20 focus:border-blue outline-none resize-y mb-2"
            />

            <label className="block text-gray text-sm mb-2">{t('deploy.negativeLabel')}</label>
            <textarea
              value={form.negative_prompt}
              onChange={e => set('negative_prompt', e.target.value)}
              placeholder={t('deploy.negativePlaceholder')}
              className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-fg text-sm h-14 focus:border-blue outline-none resize-y mb-2"
            />

            {/* Interruttore di orchestrazione AI: stessa interazione della pagina video */}
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
              {IMG_PRESETS.map(r => (
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

            <div className="grid grid-cols-3 gap-3 mb-6">
              <Field label={t('deploy.steps')} value={form.steps} onChange={v => set('steps', v)} hint={t('deploy.imageStepsHint')} />
              <Field label="CFG" value={form.cfg} onChange={v => set('cfg', v)} hint={t('deploy.cfg')} />
              <Field label={t('deploy.seed')} value={form.seed} onChange={v => set('seed', v)} placeholder={t('deploy.seedPlaceholder')} />
            </div>

            <button
              onClick={generate}
              disabled={!form.prompt || !selected || busy || (job && job.state !== 'completed')}
              className="w-full bg-gradient-to-r from-blue to-purple text-bg font-bold py-2.5 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition"
            >
              <span className="inline-flex items-center justify-center gap-1.5"><IconRocket size={15} />{busy ? t('deploy.status.running') : t('deploy.generateImage')}</span>
            </button>
            {msg && <div className="mt-3 text-sm text-red">{msg}</div>}
          </>
        )}

        {/* Avanzamento della generazione: stessa struttura della pagina video */}
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
                      <img
                        src={src}
                        alt={f.filename}
                        className="w-full rounded-lg border border-gray/30 bg-black"
                        style={{ maxHeight: '480px', objectFit: 'contain' }}
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
