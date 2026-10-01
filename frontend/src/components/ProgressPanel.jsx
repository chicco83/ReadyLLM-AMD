import { useI18n } from '../i18n/I18nContext'

// Pannello di avanzamento riutilizzabile: titolo + elenco di log in tempo reale, usato nella barra destra della pagina di tuning
// Con running=true mostra l'indicatore di attivita'; con logs vuoto mostra il segnaposto di attesa

export default function ProgressPanel({ title = 'Tuning Progress', logs = [], running = false, emptyHint = 'Waiting for task to start…' }) {
  const { t } = useI18n()
  return (
    <div className="bg-card rounded-xl p-4 border border-gray/30 lg:sticky lg:top-6">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-sm font-semibold">{title}</span>
        {running && (
          <span className="flex items-center gap-1 text-xs text-green">
            <span className="w-1.5 h-1.5 rounded-full bg-green animate-pulse" />
            {t('panel.running')}
          </span>
        )}
      </div>
      <div className="bg-bg rounded-lg p-3 h-[60vh] max-h-[640px] min-h-[280px] overflow-auto font-mono text-xs text-fg/80 space-y-0.5">
        {logs.length === 0
          ? <div className="text-gray/50">{emptyHint}</div>
          : logs.map((l, i) => (
              <div key={i}><span className="text-gray/50">[{l.t}]</span> {l.msg}</div>
            ))}
      </div>
    </div>
  )
}
