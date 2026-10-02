import { BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { useI18n } from '../i18n/I18nContext'
import BeforeAfter from './BeforeAfter'

// [2026-10-02 v1.1.36] Grafici del tuning IN TEMPO REALE: una barra per ogni prova conclusa (si aggiungono man mano), per decodifica,
// prefill e TTFT, con la linea tratteggiata della baseline; barra grigia = baseline, verde = migliore finora, blu = le altre.
// Sotto, il confronto prima/dopo che si aggiorna quando una prova supera la migliore. Dati: job.trials da GET /api/tune/active.
const GRAY = '#6c7086', GREEN = '#a6e3a1', BLUE = '#89b4fa'

function Mini({ title, unit, data, field, baseVal, lowerBetter, bestN, baseN }) {
  return (
    <div className="bg-bg rounded-lg p-3">
      <div className="text-xs text-gray mb-1">{title} <span className="text-gray/60">({unit}{lowerBetter ? ' ↓' : ''})</span></div>
      <ResponsiveContainer width="100%" height={150}>
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#3a3a4c" vertical={false} />
          <XAxis dataKey="n" stroke="#6c7086" fontSize={10} tickLine={false} />
          <YAxis stroke="#6c7086" fontSize={10} tickLine={false} width={38} />
          <Tooltip
            contentStyle={{ background: '#181825', border: '1px solid #45475a', borderRadius: 8, fontSize: 12 }}
            labelFormatter={(n) => { const r = data.find(d => d.n === n); return `#${n} ${r ? r.tag : ''}` }}
            formatter={(v) => [`${v} ${unit}`, title]} />
          {baseVal > 0 && <ReferenceLine y={baseVal} stroke={GRAY} strokeDasharray="4 3" />}
          <Bar dataKey={field} radius={[3, 3, 0, 0]} isAnimationActive animationDuration={500}>
            {data.map(d => <Cell key={d.n} fill={d.n === baseN ? GRAY : d.n === bestN ? GREEN : BLUE} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function LiveTuning({ job }) {
  const { t } = useI18n()
  const trials = (job.trials || []).filter(x => x.metrics)
  const failed = (job.trials || []).filter(x => x.failed).length
  const pr = job.progress || {}
  const baseTrial = trials.find(x => x.tag === 'baseline')
  // migliore finora per punteggio (le prove del confronto tra motori non entrano: configurazione neutra diversa)
  const cand = trials.filter(x => !/^motore/.test(x.tag))
  const best = cand.length ? cand.reduce((a, b) => (b.score > a.score ? b : a)) : null
  const data = trials.map(x => ({ n: x.n, tag: x.tag, decode: x.metrics.decode, prefill: x.metrics.prefill, ttft: x.metrics.ttft_ms }))
  const bm = baseTrial?.metrics || {}
  const toRes = (x) => x && ({ label: x.label, metrics: x.metrics, engine: x.engine })
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-gray mb-2">
        <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-green animate-pulse" />{t('impact.live')}</span>
        <span>{pr.phase}</span>
        <span>{trials.length} {t('impact.trialsDone')}{failed ? ` · ${failed} ${t('impact.trialsFailed')}` : ''}</span>
        {best && <span className="text-green">{t('impact.bestSoFar')}: {best.metrics.decode} t/s</span>}
      </div>
      {trials.length === 0 ? (
        <div className="text-sm text-gray py-6">{t('impact.waitFirst')}</div>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Mini title={t('tune.col.decode')} unit="t/s" data={data} field="decode" baseVal={bm.decode} bestN={best?.n} baseN={baseTrial?.n} />
            <Mini title={t('tune.col.prefill')} unit="t/s" data={data} field="prefill" baseVal={bm.prefill} bestN={best?.n} baseN={baseTrial?.n} />
            <Mini title="TTFT" unit="ms" data={data} field="ttft" baseVal={bm.ttft_ms} lowerBetter bestN={best?.n} baseN={baseTrial?.n} />
          </div>
          {baseTrial && best && <BeforeAfter baseline={toRes(baseTrial)} best={toRes(best)} />}
        </>
      )}
    </div>
  )
}
