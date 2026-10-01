import { useState, useEffect, useRef } from 'react'
import { IconAlert, IconCheck, IconX } from '../components/Icons'
import { useI18n } from '../i18n/I18nContext'

const EMPTY = {
  conn_type: 'local',
  os: 'windows',
  name: '',
  host: '',
  port: 22,
  user: '',
  auth_type: 'key',
  key_path: '',
  password: '',
  engine_type: 'llama_cpp',
  engine_path: '',
  llama_backend: 'auto',
  models_dir: '',
  service_port: 8080,
}

const OS_OPTIONS = [
  ['windows', 'Windows'],
  ['macos', 'macOS'],
  ['linux', 'Linux'],
]

const OS_LABEL = { windows: 'Windows', macos: 'macOS', linux: 'Linux' }

// Esempi di percorso dell'eseguibile llama.cpp per ogni sistema
function llamaPlaceholder(os) {
  if (os === 'windows') return 'C:\\llama\\llama-server.exe'
  if (os === 'macos') return '/opt/homebrew/bin/llama-server'
  return '/usr/local/bin/llama-server'
}
// Segnaposto del percorso del motore: vLLM / SGLang usano il nome del comando, llama.cpp il percorso completo
function enginePlaceholder(engineType, os, t) {
  if (engineType === 'vllm') return t('settings.vllmPlaceholder')
  if (engineType === 'sglang') return t('settings.sglangPlaceholder')
  return llamaPlaceholder(os)
}
// Testo dei metadati del motore: ha priorita' engine.<type>.<field> della lingua corrente, se la chiave non c'e' ripiega sul valore inviato dal backend
// (se manca la chiave t() restituisce la chiave stessa, e su questo si decide se c'e' stata corrispondenza)
function engineText(engineType, field, t, fallback = '') {
  if (!engineType) return fallback
  const key = `engine.${engineType}.${field}`
  const s = t(key)
  return s === key ? fallback : s
}
function modelsPlaceholder(os) {
  if (os === 'windows') return 'D:\\models'
  if (os === 'macos') return '~/models'
  return '/home/user/models'
}

function Field({ label, children, hint }) {
  return (
    <div className="mb-4">
      <label className="block text-sm text-gray mb-1">{label}</label>
      {children}
      {hint && <div className="text-xs text-gray/70 mt-1">{hint}</div>}
    </div>
  )
}

const inputCls =
  'w-full bg-bg border border-gray/40 rounded-lg px-3 py-2 text-fg focus:border-blue outline-none'

function SegButtons({ value, options, onChange }) {
  return (
    <div className="flex gap-3">
      {options.map(([v, l]) => (
        <button
          key={v}
          onClick={() => onChange(v)}
          className={`flex-1 py-2 rounded-lg border transition ${
            value === v ? 'border-blue bg-blue/20 text-blue' : 'border-gray/40 text-fg/70'
          }`}
        >
          {l}
        </button>
      ))}
    </div>
  )
}

export default function Settings({ targets, onSaved, onChanged }) {
  const { t } = useI18n()
  const [form, setForm] = useState(EMPTY)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [localOs, setLocalOs] = useState('')
  const [engines, setEngines] = useState([])

  function set(k, v) {
    setForm(f => ({ ...f, [k]: v }))
  }

  const isRemote = form.conn_type === 'ssh'

  // [2026-10-01 v1.1.17] Backend, build installate e installazione in un unico punto: i pulsanti del backend in alto.
  // - ogni pulsante mostra se la build di quel backend e' installata
  // - cliccarlo la mette in uso (cambia da solo il percorso del motore e salva)
  // - se non e' installata compare «Installa» subito sotto, con i log
  const [builds, setBuilds] = useState([])
  const [inst, setInst] = useState({ running: false, backend: '', logs: [] })
  const instPollRef = useRef(null)

  async function loadBuilds() {
    if (!form.id || form.engine_type !== 'llama_cpp') { setBuilds([]); return }
    try {
      const r = await fetch(`/api/target/${form.id}/engines-installed`)
      const d = await r.json()
      setBuilds(d.builds || [])
    } catch { /* ignora */ }
  }
  useEffect(() => { loadBuilds(); return () => clearInterval(instPollRef.current) }, [form.id, form.engine_type])

  const buildFor = (b) => builds.find(x => (x.backend || '').replace('?', '').split('+').includes(b))

  async function pickBackend(v) {
    const b = v === 'auto' ? null : buildFor(v)
    setForm(f => ({ ...f, llama_backend: v, engine_path: b ? b.path : f.engine_path }))
    if (form.id) {
      const r = await fetch(`/api/target/${form.id}/activate-engine`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: b ? b.path : null, llama_backend: v }),
      })
      const d = await r.json()
      if (d.ok) { if (onSaved) onSaved(d.targets); loadBuilds() }
    }
  }

  async function installBackend(v) {
    if (!form.id) { setTestResult({ ok: false, message: t('settings.saveFirst') }); return }
    setInst({ running: true, backend: v, logs: [] })
    const res = await fetch('/api/target/install-engine', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_id: form.id, backend: v }),
    })
    const d = await res.json()
    if (!d.ok) { setInst({ running: false, backend: v, logs: [{ t: '--:--:--', msg: d.message || t('settings.installFail') }] }); return }
    instPollRef.current = setInterval(async () => {
      const job = await (await fetch(`/api/target/install-status/${d.job_id}`)).json()
      setInst({ running: job.status === 'running', backend: v, logs: job.logs || [] })
      if (job.status === 'success' || job.status === 'failed') {
        clearInterval(instPollRef.current)
        setInst({ running: false, backend: v, logs: job.logs || [] })
        if (job.status === 'success') {
          // La build appena installata diventa quella in uso (l'installer ha gia' salvato engine_path nel backend)
          setForm(f => ({ ...f, llama_backend: v, engine_path: job.engine_path || f.engine_path }))
          await loadBuilds()
          if (onChanged) onChanged()
        }
      }
    }, 2000)
  }
  // Metadati del motore attuale (dalla fonte unica di dati del backend)
  const curEngine = engines.find(e => e.type === form.engine_type)
  // Il motore non supporta il sistema operativo di destinazione attuale? (es. vLLM + Windows)
  const engineOsUnsupported =
    curEngine && curEngine.supported_os && !curEngine.supported_os.includes(form.os)

  // Rileva il sistema operativo della macchina locale, in modalita' locale lo riconosce da solo, senza scelta manuale
  useEffect(() => {
    fetch('/api/target/local-os')
      .then(r => r.json())
      .then(d => {
        if (d.os) {
          setLocalOs(d.os)
          setForm(f => (f.conn_type === 'local' ? { ...f, os: d.os } : f))
        }
      })
      .catch(() => {})
    // Recupera i metadati dei motori disponibili
    fetch('/api/target/engines')
      .then(r => r.json())
      .then(d => setEngines(d.engines || []))
      .catch(() => {})
  }, [])

  // Quando si cambia il tipo di connessione sincronizza il sistema: locale -> valore rilevato, remoto -> mantiene/default
  function switchConn(v) {
    setForm(f => ({ ...f, conn_type: v, os: v === 'local' && localOs ? localOs : f.os }))
  }

  async function test() {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await fetch('/api/target/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      setTestResult(await res.json())
    } catch (e) {
      setTestResult({ ok: false, message: String(e) })
    } finally {
      setTesting(false)
    }
  }

  // [2026-10-01 v1.1.0] Fix bug targets.json: il form partiva sempre da EMPTY senza `id`,
  // quindi ogni salvataggio creava una nuova entry. Ora si puo' selezionare una macchina
  // salvata (il form include il suo `id`) e il backend aggiorna invece di aggiungere.
  // Versione precedente (sostituita): save() inviava form senza id, vedi git history.
  function editTarget(tg) {
    setForm({ ...EMPTY, ...tg, password: '' })
    setTestResult(null)
  }
  function newTarget() {
    setForm({ ...EMPTY, os: localOs || EMPTY.os })
    setTestResult(null)
  }

  // [2026-10-01 v1.1.10] Finestra nativa di Windows per scegliere file/cartella (solo target locale: il backend apre
  // la finestra sulla stessa macchina). Per i target remoti il percorso si digita a mano.
  async function browse(kind, field) {
    try {
      const res = await fetch('/api/target/pick', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, title: kind === 'file' ? t('settings.pickEngine') : t('settings.pickModels') }),
      })
      const d = await res.json()
      if (!d.ok) { setTestResult({ ok: false, message: d.message }); return }
      if (d.path) set(field, d.path)
    } catch (e) {
      setTestResult({ ok: false, message: String(e) })
    }
  }

  async function save() {
    const res = await fetch('/api/target', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
    const d = await res.json()
    if (d.ok) {
      // Dopo il salvataggio il form resta sulla entry salvata (con id): salvataggi successivi aggiornano
      setForm(f => ({ ...f, id: d.id }))
      if (onSaved) onSaved(d.targets)
      setTestResult(null)
    }
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-2">{t('settings.title')}</h1>
      <p className="text-gray text-sm mb-6">
        {t('settings.subtitle')}
      </p>

      {/* [2026-10-01 v1.1.0] Selettore delle macchine salvate: modifica o nuova */}
      {targets?.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <span className="text-sm text-gray">{t('settings.savedTargets')}</span>
          {targets.map(tg => (
            <button
              key={tg.id}
              onClick={() => editTarget(tg)}
              className={`px-3 py-1 rounded-lg border text-sm transition ${
                form.id === tg.id ? 'border-blue bg-blue/20 text-blue' : 'border-gray/40 text-fg/70'
              }`}
            >
              {tg.name || tg.host || tg.id}
            </button>
          ))}
          <button
            onClick={newTarget}
            className={`px-3 py-1 rounded-lg border text-sm transition ${
              !form.id ? 'border-green bg-green/20 text-green' : 'border-gray/40 text-fg/70'
            }`}
          >
            + {t('settings.newTarget')}
          </button>
        </div>
      )}

      <div className="bg-card rounded-xl p-6 border border-gray/30">
        <Field label={t('settings.name')}>
          <input className={inputCls} placeholder={t('settings.defaultName')} value={form.name} onChange={e => set('name', e.target.value)} />
        </Field>

        <Field label={t('settings.conn')}>
          <SegButtons value={form.conn_type} options={[['local', t('settings.local')], ['ssh', t('settings.ssh')]]} onChange={switchConn} />
        </Field>

        <Field label={t('settings.os')}>
          {form.conn_type === 'local' ? (
            <div className="flex items-center gap-2 py-2 px-3 rounded-lg bg-bg border border-gray/40">
              <span className="w-2 h-2 rounded-full bg-green" />
              <span className="text-fg">
                {localOs ? OS_LABEL[localOs] : t('settings.detecting')}
                <span className="text-gray text-xs ml-2">{t('settings.autoDetected')}</span>
              </span>
            </div>
          ) : (
            <SegButtons value={form.os} options={OS_OPTIONS} onChange={v => set('os', v)} />
          )}
        </Field>

        {isRemote && (
          <div className="border-l-2 border-blue/40 pl-4 my-4">
            <Field label={t('settings.host')}>
              <input className={inputCls} placeholder="192.168.1.100" value={form.host} onChange={e => set('host', e.target.value)} />
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label={t('settings.sshPort')}>
                <input type="number" className={inputCls} value={form.port} onChange={e => set('port', +e.target.value)} />
              </Field>
              <Field label={t('settings.user')}>
                <input className={inputCls} value={form.user} onChange={e => set('user', e.target.value)} />
              </Field>
            </div>
            <Field label={t('settings.auth')}>
              <SegButtons value={form.auth_type} options={[['key', t('settings.key')], ['password', t('settings.password')]]} onChange={v => set('auth_type', v)} />
            </Field>
            {form.auth_type === 'key' ? (
              <Field label={t('settings.keyPath')} hint={t('settings.keyPathHint')}>
                <input className={inputCls} placeholder="~/.ssh/id_rsa" value={form.key_path} onChange={e => set('key_path', e.target.value)} />
              </Field>
            ) : (
              <Field label={t('settings.password')}>
                <input type="password" className={inputCls} value={form.password} onChange={e => set('password', e.target.value)} />
              </Field>
            )}
          </div>
        )}

        <Field label={t('settings.engine')}>
          <SegButtons
            value={form.engine_type}
            options={
              engines.length > 0
                ? engines.map(e => [e.type, e.label])
                : [['llama_cpp', 'llama.cpp'], ['vllm', 'vLLM']]
            }
            onChange={v => set('engine_type', v)}
          />
          {engineText(form.engine_type, 'desc', t, curEngine?.desc) && (
            <div className="text-xs text-gray/70 mt-2">
              {engineText(form.engine_type, 'desc', t, curEngine?.desc)}
            </div>
          )}
        </Field>

        {engineOsUnsupported && (
          <div className="mb-4 p-3 rounded-lg bg-yellow/10 text-yellow text-sm border border-yellow/30 flex items-start gap-2">
            <span className="mt-0.5 shrink-0"><IconAlert size={15} /></span>
            <span>{engineText(form.engine_type, 'windowsNote', t, curEngine?.windows_note) || t('settings.engineUnsupported', { os: OS_LABEL[form.os] })}</span>
          </div>
        )}

        <Field
          label={
            form.engine_type === 'vllm'
              ? t('settings.vllmPath')
              : form.engine_type === 'sglang'
                ? t('settings.sglangPath')
                : t('settings.enginePath')
          }
          hint={
            form.engine_type === 'vllm'
              ? t('settings.vllmPathHint')
              : form.engine_type === 'sglang'
                ? t('settings.sglangPathHint')
                : t('settings.enginePathHint')
          }
        >
          <div className="flex gap-2">
            <input
              className={inputCls}
              placeholder={enginePlaceholder(form.engine_type, form.os, t)}
              value={form.engine_path}
              onChange={e => set('engine_path', e.target.value)}
            />
            {/* Sfoglia: solo target locale e motori con eseguibile (llama.cpp) */}
            {!isRemote && form.engine_type === 'llama_cpp' && (
              <button type="button" onClick={() => browse('file', 'engine_path')}
                className="shrink-0 px-3 rounded-lg border border-blue text-blue text-sm hover:bg-blue/10 transition">
                {t('settings.browse')}
              </button>
            )}
          </div>
        </Field>

        {/* [2026-10-01 v1.1.0] Scelta backend llama.cpp (ROCm / Vulkan / CUDA / CPU)
            [2026-10-01 v1.1.17] Integrato con build installate: il pulsante mette in uso la build, o propone di installarla.
            Versione precedente: SegButtons semplici + sezione «Build installate / Installa» separata sotto «Motori di inferenza». */}
        {form.engine_type === 'llama_cpp' && (
          <Field label={t('settings.llamaBackend')} hint={t('settings.llamaBackendHint')}>
            <div className="grid grid-cols-5 gap-2">
              {[['auto', t('settings.backend.auto')], ['cuda', 'CUDA'], ['rocm', 'ROCm (HIP)'], ['vulkan', 'Vulkan'], ['cpu', 'CPU']].map(([v, l]) => {
                const sel = (form.llama_backend || 'auto') === v
                const have = v !== 'auto' && !!buildFor(v)
                return (
                  <button key={v} type="button" onClick={() => pickBackend(v)}
                    className={`py-2 rounded-lg border transition ${sel ? 'border-blue bg-blue/20 text-blue' : 'border-gray/40 text-fg/70'}`}>
                    <div>{l}</div>
                    {v !== 'auto' && form.id && (
                      <div className={`text-[11px] ${have ? 'text-green' : 'text-gray/60'}`}>
                        {have ? `✓ ${t('settings.installedShort')}` : t('settings.notInstalledShort')}
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
            {(() => {
              const v = form.llama_backend || 'auto'
              if (v === 'auto' || !form.id) return null
              const b = buildFor(v)
              return (
                <div className="mt-3 text-sm">
                  {b ? (
                    <div className="text-green text-xs truncate">{t('settings.inUse')}: {b.path}</div>
                  ) : (
                    <div className="flex items-center gap-3 p-3 rounded-lg bg-yellow/10 border border-yellow/30">
                      <span className="text-yellow flex-1">{t('settings.backendMissing', { b: v })}</span>
                      <button type="button" onClick={() => installBackend(v)} disabled={inst.running}
                        className="bg-blue text-bg font-semibold px-4 py-1.5 rounded-lg hover:opacity-90 transition disabled:opacity-40">
                        {inst.running ? t('settings.badge.installing') : t('settings.install')}
                      </button>
                    </div>
                  )}
                  {inst.logs.length > 0 && (
                    <div className="mt-2 bg-bg rounded-lg p-3 max-h-48 overflow-auto font-mono text-xs text-fg/80 space-y-0.5">
                      {inst.logs.map((l, i) => (<div key={i}><span className="text-gray/50">[{l.t}]</span> {l.msg}</div>))}
                    </div>
                  )}
                </div>
              )
            })()}
            {!form.id && <div className="text-xs text-yellow mt-2">{t('settings.saveFirst')}</div>}
          </Field>
        )}

        <Field
          label={t('settings.modelsDir')}
          hint={
            form.engine_type === 'vllm'
              ? t('settings.modelsDirVllm')
              : form.engine_type === 'sglang'
                ? t('settings.modelsDirSglang')
                : t('settings.modelsDirLlama')
          }
        >
          <div className="flex gap-2">
            <input
              className={inputCls}
              placeholder={modelsPlaceholder(form.os)}
              value={form.models_dir}
              onChange={e => set('models_dir', e.target.value)}
            />
            {!isRemote && (
              <button type="button" onClick={() => browse('folder', 'models_dir')}
                className="shrink-0 px-3 rounded-lg border border-blue text-blue text-sm hover:bg-blue/10 transition">
                {t('settings.browse')}
              </button>
            )}
          </div>
        </Field>

        <Field label={t('settings.servicePort')} hint={t('settings.servicePortHint')}>
          <input type="number" className={inputCls} value={form.service_port} onChange={e => set('service_port', +e.target.value)} />
        </Field>

        <div className="flex gap-3 mt-6">
          <button
            onClick={test}
            disabled={testing}
            className="flex-1 py-2 rounded-lg border border-blue text-blue font-semibold disabled:opacity-40 hover:bg-blue/10 transition"
          >
            {testing ? t('settings.testing') : t('settings.testConn')}
          </button>
          <button
            onClick={save}
            className="flex-1 py-2 rounded-lg bg-green text-bg font-bold hover:opacity-90 transition"
          >
            {t('settings.save')}
          </button>
        </div>

        {testResult && (
          <div className={`mt-4 p-3 rounded-lg text-sm ${testResult.ok ? 'bg-green/10 text-green' : 'bg-red/10 text-red'}`}>
            <div className="font-semibold mb-1 inline-flex items-center gap-1.5">{testResult.ok ? <><IconCheck size={15} />{t('settings.connOk')}</> : <><IconX size={15} />{t('settings.connFail')}</>}</div>
            <div>{testResult.message}</div>
            {testResult.hardware?.gpu && (
              <div className="mt-2 text-fg/80">
                GPU: {testResult.hardware.gpu.name} ({testResult.hardware.gpu.total_memory_gb}G) ·
                {t('settings.memory')} {testResult.hardware.memory?.total_gb}G
              </div>
            )}
          </div>
        )}
      </div>

      {/* Pannello di installazione dei motori */}
      {targets?.length > 0 && (
        <div className="mt-8">
          <h2 className="text-xl font-bold mb-2">{t('settings.engines')}</h2>
          <p className="text-gray text-sm mb-4">
            {t('settings.enginesHint')}
          </p>
          <div className="space-y-3">
            {targets.map(tg => (
              <EngineRow key={tg.id} target={tg} onChanged={onChanged} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function EngineRow({ target, onChanged }) {
  const { t } = useI18n()
  const [state, setState] = useState('checking') // checking | installed | missing | installing
  const [engine, setEngine] = useState(null)
  const [logs, setLogs] = useState([])
  const [showLogs, setShowLogs] = useState(false)
  const pollRef = useRef(null)

  async function check() {
    setState('checking')
    try {
      const res = await fetch(`/api/target/${target.id}/engine`)
      const d = await res.json()
      setEngine(d)
      setState(d.installed ? 'installed' : 'missing')
    } catch {
      setState('missing')
    }
  }

  useEffect(() => {
    check()
    return () => clearInterval(pollRef.current)
  }, [target.id, target.engine_path])

  async function install() {
    setState('installing')
    setLogs([])
    setShowLogs(true)
    const res = await fetch('/api/target/install-engine', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_id: target.id }),
    })
    const d = await res.json()
    if (!d.ok) {
      setLogs([{ t: '--:--:--', msg: d.message || t('settings.installFail') }])
      setState('missing')
      return
    }
    const jobId = d.job_id
    pollRef.current = setInterval(async () => {
      const sr = await fetch(`/api/target/install-status/${jobId}`)
      const job = await sr.json()
      setLogs(job.logs || [])
      if (job.status === 'success') {
        clearInterval(pollRef.current)
        setEngine({ installed: true, path: job.engine_path, version: '' })
        setState('installed')
        if (onChanged) onChanged()
      } else if (job.status === 'failed') {
        clearInterval(pollRef.current)
        setState('missing')
      }
    }, 2000)
  }

  const badge = {
    checking: { text: t('settings.badge.checking'), cls: 'bg-gray/20 text-gray' },
    installed: { text: t('settings.badge.installed'), cls: 'bg-green/20 text-green' },
    missing: { text: t('settings.badge.missing'), cls: 'bg-yellow/20 text-yellow' },
    installing: { text: t('settings.badge.installing'), cls: 'bg-blue/20 text-blue' },
  }[state]

  return (
    <div className="bg-card rounded-xl p-4 border border-gray/30">
      <div className="flex items-center justify-between">
        <div>
          <div className="font-semibold">
            {target.name}
            <span className="ml-2 text-xs px-2 py-0.5 rounded bg-blue/15 text-blue font-normal">
              {target.engine_type === 'vllm' ? 'vLLM' : 'llama.cpp'}
            </span>
          </div>
          <div className="text-xs text-gray">
            {target.os} · {target.conn_type === 'ssh' ? target.host : t('settings.local')}
            {engine?.version && <span className="ml-2 text-green">{engine.version}</span>}
          </div>
          {/* [2026-10-01 v1.1.10] Backend del llama-server installato (Vulkan / ROCm / CUDA / CPU) e dispositivi rilevati */}
          {engine?.installed && engine.engine === 'llama_cpp' && (
            <div className="text-xs mt-1">
              <span className="text-gray">{t('settings.backendDetected')}</span>{' '}
              <span className="px-2 py-0.5 rounded bg-purple/15 text-purple font-semibold uppercase">
                {engine.backend || 'cpu'}
              </span>
              {(engine.devices || []).map((d, i) => (
                <span key={i} className="ml-2 text-gray/70">{d}</span>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className={`text-xs px-2 py-1 rounded-full ${badge.cls}`}>{badge.text}</span>
          {/* llama.cpp: il pulsante Installa e' nella sezione build (sotto), sempre disponibile; qui resta per gli altri motori */}
          {state === 'missing' && target.engine_type !== 'llama_cpp' && (
            <button
              onClick={install}
              className="bg-blue text-bg text-sm font-semibold px-4 py-1.5 rounded-lg hover:opacity-90 transition"
            >
              {t('settings.install')}
            </button>
          )}
          {state === 'installed' && (
            <button onClick={check} className="text-gray text-sm hover:text-fg transition">{t('settings.recheck')}</button>
          )}
        </div>
      </div>

      {engine?.installed && engine.path && (
        <div className="text-xs text-gray/70 mt-2 truncate">{t('settings.path')} {engine.path}</div>
      )}

      {/* [2026-10-01 v1.1.17] Build installate / Attiva / Installa per backend: spostate nel form, sotto i pulsanti del backend */}

      {(showLogs && logs.length > 0) && (
        <div className="mt-3">
          <button onClick={() => setShowLogs(s => !s)} className="text-xs text-gray mb-1">
            {showLogs ? t('settings.hideLogs') : t('settings.showLogs')}
          </button>
          <div className="bg-bg rounded-lg p-3 max-h-48 overflow-auto font-mono text-xs text-fg/80 space-y-0.5">
            {logs.map((l, i) => (
              <div key={i}><span className="text-gray/50">[{l.t}]</span> {l.msg}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
