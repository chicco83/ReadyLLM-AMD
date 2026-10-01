import { useI18n } from '../i18n/I18nContext'

// [2026-10-02 v1.1.27] Grafico «prima vs dopo» del tuning: per decodifica, prefill e TTFT due barre affiancate (la tua configurazione
// di partenza contro quella consigliata) con la variazione in %. Per il TTFT piu' basso e' meglio. Mostra anche il motore usato
// nelle due misure (es. VULKAN -> ROCM). Usato nella barra di progresso a fine tuning e nel riquadro della configurazione consigliata.
function Row({ label, unit, before, after, lowerBetter = false }) {
  if (before == null || after == null || (!before && !after)) return null
  const max = Math.max(before, after, 1e-9)
  const delta = before > 0 ? ((after - before) / before) * 100 : 0
  const good = lowerBetter ? delta <= 0 : delta >= 0
  const wB = (before / max) * 100
  const wA = (after / max) * 100
  return (
    <div className="mb-3">
      <div className="flex items-baseline justify-between text-xs mb-1">
        <span className="text-gray">{label}</span>
        <span className={`font-bold text-sm ${Math.abs(delta) < 0.5 ? 'text-gray' : good ? 'text-green' : 'text-red'}`}>
          {delta > 0 ? '+' : ''}{delta.toFixed(1)}%
        </span>
      </div>
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <div className="h-4 rounded bg-gray/50" style={{ width: `${wB}%`, minWidth: 4 }} />
          <span className="text-xs text-gray whitespace-nowrap">{before} {unit}</span>
        </div>
        <div className="flex items-center gap-2">
          <div className={`h-4 rounded ${good ? 'bg-green' : 'bg-red'}`} style={{ width: `${wA}%`, minWidth: 4 }} />
          <span className="text-xs font-semibold whitespace-nowrap">{after} {unit}</span>
        </div>
      </div>
    </div>
  )
}

export default function BeforeAfter({ baseline, best }) {
  const { t } = useI18n()
  if (!baseline?.metrics || !best?.metrics) return null
  const eb = baseline.engine?.backend, ea = best.engine?.backend
  const same = best === baseline || best.label === baseline.label
  return (
    <div className="bg-bg rounded-xl p-4 mt-3">
      <div className="flex items-center justify-between text-xs mb-3 gap-3 flex-wrap">
        <span className="inline-flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-gray/50" />
          <b>{t('tune.before')}</b> {eb ? <span className="uppercase text-purple">{eb}</span> : null}</span>
        <span className="inline-flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-green" />
          <b>{t('tune.after')}</b> {ea ? <span className="uppercase text-purple">{ea}</span> : null}
          {eb && ea && eb !== ea ? <span className="text-yellow">({t('tune.engineChanged')})</span> : null}</span>
      </div>
      {same && <div className="text-xs text-gray mb-2">{t('tune.noBetter')}</div>}
      <Row label={t('tune.col.decode')} unit="t/s" before={baseline.metrics.decode} after={best.metrics.decode} />
      <Row label={t('tune.col.prefill')} unit="t/s" before={baseline.metrics.prefill} after={best.metrics.prefill} />
      {/* [2026-10-02 v1.1.28] prefill su ~16k token (misurato solo con l'obiettivo Coding) */}
      <Row label={t('tune.col.prefillLong')} unit="t/s" before={baseline.metrics.prefill_long} after={best.metrics.prefill_long} />
      <Row label={t('tune.col.ttft')} unit="ms" before={baseline.metrics.ttft_ms} after={best.metrics.ttft_ms} lowerBetter />
    </div>
  )
}
