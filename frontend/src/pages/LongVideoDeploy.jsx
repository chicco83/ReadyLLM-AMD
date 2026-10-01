import { useState, useEffect, useRef } from 'react'
import { IconPlay, IconStop, IconRocket } from '../components/Icons'

/* ==================== Deploy video lungo (storyboard -> I2V segmento per segmento -> concatenazione) ==================== */

const STATE_COLOR = {
  pending: 'bg-gray/40',
  running: 'bg-blue animate-pulse',
  completed: 'bg-green',
  failed: 'bg-red',
  interrupted: 'bg-yellow',
}
const STATE_TEXT = {
  pending: 'Da generare',
  running: 'In generazione…',
  completed: 'Completato',
  failed: 'Fallito',
  interrupted: 'Interrotto',
}

export default function LongVideoDeploy({ targetId, target }) {
  const [status, setStatus] = useState(null)
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState({
    theme: '',
    total_seconds: 30,
    max_shots: 6,
    seed_image_path: '',
    width: 832,
    height: 480,
    steps: 8,
    cfg: 1.0,
    fps: 16,
  })
  const [storyboard, setStoryboard] = useState(null)
  const [sbLoading, setSbLoading] = useState(false)
  const [job, setJob] = useState(null) // {job_id, status, shots, final_file}
  const pollRef = useRef(null)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  useEffect(() => {
    if (!targetId) return
    setMsg('')
    fetch(`/api/deploy/status?target_id=${targetId}`).then(r => r.json()).then(setStatus)
  }, [targetId])

  // Polling dell'avanzamento segmento per segmento
  useEffect(() => {
    if (!job?.job_id || job.status === 'completed' || job.status === 'failed' || job.status === 'not_found') {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
      return
    }
    pollRef.current = setInterval(async () => {
      const res = await fetch(`/api/deploy/long-video/progress?job_id=${job.job_id}`)
      const d = await res.json()
      setJob(prev => ({ ...prev, ...d }))
    }, 6000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [job?.job_id, job?.status])

  async function startEngine() {
    setMsg('Avvio di ComfyUI in corso…')
    const res = await fetch('/api/deploy/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_id: targetId, model: 'comfyui' }),
    })
    const d = await res.json()
    setMsg(d.message || '')
    setTimeout(() => fetch(`/api/deploy/status?target_id=${targetId}`).then(r => r.json()).then(setStatus), 5000)
  }

  async function makeStoryboard() {
    if (!form.theme.trim()) { setMsg('Inserisci prima il tema del video'); return }
    setSbLoading(true); setMsg(''); setStoryboard(null)
    const res = await fetch('/api/deploy/storyboard', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme: form.theme, total_seconds: Number(form.total_seconds), max_shots: Number(form.max_shots) }),
    })
    const d = await res.json()
    setSbLoading(false)
    if (!d.success) { setMsg(d.message || 'Generazione dello storyboard non riuscita'); return }
    setStoryboard(d.storyboard)
  }

  async function submit() {
    if (!storyboard) { setMsg('Genera prima lo storyboard'); return }
    setMsg(''); setJob(null)
    const res = await fetch('/api/deploy/long-video', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_id: targetId,
        storyboard,
        seed_image_path: form.seed_image_path.trim(),
        width: Number(form.width), height: Number(form.height),
        steps: Number(form.steps), cfg: Number(form.cfg), fps: Number(form.fps),
      }),
    })
    const d = await res.json()
    if (!d.success) { setMsg(d.message || 'Invio non riuscito'); return }
    setJob({ job_id: d.job_id, status: 'running', shots: storyboard.shots.map(s => ({ ...s, state: 'pending' })) })
  }

  const doneCount = job?.shots?.filter(s => s.state === 'completed').length || 0
  const total = job?.shots?.length || 0

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Generazione di video lunghi</h1>

      <div className="bg-card rounded-xl p-6 border border-gray/30 max-w-2xl">
        <div className="flex items-center gap-3 mb-6">
          <span className={`w-3 h-3 rounded-full ${status?.running ? 'bg-green' : 'bg-gray'}`} />
          <span className="font-semibold">{status?.running ? 'ComfyUI in esecuzione' : 'ComfyUI non in esecuzione'}</span>
          <span className="text-xs px-2 py-0.5 rounded bg-purple/15 text-purple">Motore: ComfyUI</span>
        </div>

        {!status?.running ? (
          <div className="mb-2">
            <div className="text-sm text-gray mb-3">
              Per i video lunghi bisogna prima avviare ComfyUI. La generazione e' seriale segmento per segmento: 1 minuto richiede circa 6-12 segmenti, tempo totale di alcuni minuti.
            </div>
            <button onClick={startEngine}
              className="w-full bg-green text-bg font-bold py-2 rounded-lg hover:opacity-90 transition">
              <span className="inline-flex items-center justify-center gap-1.5"><IconPlay size={14} />Avvia ComfyUI</span>
            </button>
            {msg && <div className="mt-3 text-sm text-gray">{msg}</div>}
          </div>
        ) : (
          <>
            <label className="block text-gray text-sm mb-2">Tema del video / sinossi della storia</label>
            <textarea
              value={form.theme}
              onChange={e => set('theme', e.target.value)}
              placeholder="Un astronauta scopre sulla superficie di Marte una antica porta di legno"
              className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-fg text-sm h-20 focus:border-blue outline-none resize-y mb-4"
            />

            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-gray text-sm mb-2">Durata obiettivo (secondi)</label>
                <input type="number" value={form.total_seconds}
                  onChange={e => set('total_seconds', e.target.value)}
                  className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-fg" />
              </div>
              <div>
                <label className="block text-gray text-sm mb-2">Numero massimo di inquadrature</label>
                <input type="number" value={form.max_shots}
                  onChange={e => set('max_shots', e.target.value)}
                  className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-fg" />
              </div>
            </div>

            <label className="block text-gray text-sm mb-2">Immagine del primo fotogramma seed (facoltativa, percorso assoluto locale)</label>
            <input type="text" value={form.seed_image_path}
              onChange={e => set('seed_image_path', e.target.value)}
              placeholder="Se vuoto il 1° segmento usa il puro text-to-video; i segmenti successivi si raccordano automaticamente con l'ultimo fotogramma del precedente"
              className="w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-fg text-sm mb-5" />

            <button onClick={makeStoryboard} disabled={sbLoading}
              className="w-full bg-blue text-bg font-bold py-2 rounded-lg hover:opacity-90 transition disabled:opacity-40 mb-2">
              {sbLoading ? 'Il modello sta scomponendo lo storyboard…' : 'Genera lo storyboard'}
            </button>
            {msg && <div className="mt-2 text-sm text-gray">{msg}</div>}
          </>
        )}
      </div>

      {/* Anteprima dello storyboard */}
      {storyboard && (
        <div className="bg-card rounded-xl p-6 border border-gray/30 max-w-2xl mt-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-bold text-lg">{storyboard.title}</h2>
            <span className="text-xs text-gray">{storyboard.shots.length} inquadrature</span>
          </div>
          <div className="space-y-3 mb-5">
            {storyboard.shots.map(s => (
              <div key={s.index} className="p-3 rounded-lg bg-bg border border-gray/30">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-bold text-blue">#{s.index}</span>
                  <span className="text-sm font-semibold text-fg">{s.title}</span>
                  <span className="text-[11px] text-gray/70 ml-auto">{Math.round(s.length / s.fps || s.length / 16)}s · {s.length} fot.</span>
                </div>
                <div className="text-xs text-gray/80 italic leading-relaxed mb-1">{s.prompt}</div>
                {s.continuity && <div className="text-[11px] text-gray/60">Raccordo: {s.continuity}</div>}
              </div>
            ))}
          </div>
          <button onClick={submit} disabled={!!job}
            className="w-full bg-purple text-bg font-bold py-2 rounded-lg hover:opacity-90 transition disabled:opacity-40">
            <span className="inline-flex items-center justify-center gap-1.5"><IconRocket size={14} />Avvia la generazione segmento per segmento e la concatenazione</span>
          </button>
        </div>
      )}

      {/* Avanzamento segmento per segmento */}
      {job && (
        <div className="bg-card rounded-xl p-6 border border-gray/30 max-w-2xl mt-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-bold text-lg">Avanzamento della generazione</h2>
            <span className="text-xs text-gray">{doneCount}/{total} segmenti completati</span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-gray/30 mb-4 overflow-hidden">
            <div className="h-full bg-green transition-all" style={{ width: `${total ? doneCount / total * 100 : 0}%` }} />
          </div>
          <div className="space-y-2 mb-4">
            {job.shots?.map(s => (
              <div key={s.index} className="flex items-center gap-2 text-sm">
                <span className={`w-2.5 h-2.5 rounded-full ${STATE_COLOR[s.state] || 'bg-gray'}`} />
                <span className="text-fg">#{s.index} {s.title}</span>
                <span className="text-xs text-gray ml-auto">{STATE_TEXT[s.state] || s.state}</span>
              </div>
            ))}
          </div>
          {job.error && <div className="text-sm text-red mb-3">{job.error}</div>}
          {job.status === 'completed' && job.final_file && (
            <div className="mt-2">
              <div className="text-sm font-semibold text-green mb-2">Video finale generato</div>
              <video
                src={`/api/deploy/video-file?target_id=${targetId}&filename=${encodeURIComponent(job.final_file)}&subfolder=modeldeploy`}
                controls autoPlay loop playsInline
                className="w-full rounded-lg border border-gray/40" />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
