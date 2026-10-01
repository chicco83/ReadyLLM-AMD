import { useState, useEffect } from 'react'
import { useI18n } from '../i18n/I18nContext'

// [2026-10-02 v1.1.29] Scheda «Decodifica speculativa»: mostra se il modello in esecuzione usa MTP o ngram, con che parametri,
// il tasso di accettazione (dalle metriche) e DOVE vive in memoria. Dati da GET /api/deploy/spec (parametri dell'ultimo avvio dal
// Deploy). Chiarimento importante: MTP NON usa la RAM di sistema (le teste MTP sono nel modello, quindi in VRAM) e ngram NON usa
// l'SSD (cerca nella cronologia dei token in RAM/CPU): nessuno dei due tocca il disco.
export default function SpecCard({ targetId, acceptRate }) {
  const { t } = useI18n()
  const [info, setInfo] = useState(null)
  useEffect(() => {
    if (!targetId) return
    const load = () => fetch(`/api/deploy/spec?target_id=${targetId}`).then(r => r.json()).then(setInfo).catch(() => {})
    load()
    const id = setInterval(load, 4000)   // [2026-10-02 v1.1.32] piu' frequente: durante il tuning il tipo cambia a ogni prova
    return () => clearInterval(id)
  }, [targetId])

  const type = info?.known ? info.type : null
  const isMtp = type && type.includes('mtp')
  const isNgram = type && type.includes('ngram')
  const isDraft = type && type.startsWith('draft-') && !isMtp
  const label = !info?.known ? t('spec.unknown') : isMtp ? 'MTP (draft-mtp)' : isNgram ? `ngram (${type})` : isDraft ? type : t('spec.none')
  const where = isMtp
    ? (info.draft_ngl && info.draft_ngl !== 'all' && info.draft_ngl !== '99' ? `${t('spec.whereMtpPart')} (draft ngl=${info.draft_ngl})` : t('spec.whereMtp'))
    : isNgram ? t('spec.whereNgram') : isDraft ? t('spec.whereDraft') : ''
  return (
    <div className="bg-card rounded-xl p-4 border border-gray/30 mb-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
        <span className="font-semibold text-teal">{t('spec.title')}</span>
        <span className={`px-2 py-0.5 rounded text-xs font-semibold ${type && type !== 'none' ? 'bg-teal/20 text-teal' : 'bg-gray/20 text-gray'}`}>{label}</span>
        {info?.known && info.n_max && <span className="text-gray text-xs">n-max {info.n_max}{info.n_min ? ` · n-min ${info.n_min}` : ''}</span>}
        {type && type !== 'none' && <span className="text-xs"><span className="text-gray">{t('spec.accept')}</span> <b>{acceptRate != null ? `${acceptRate}%` : '--'}</b></span>}
        {info?.known && <span className="text-gray text-xs">KV: {info.kv_k}/{info.kv_v}</span>}
      </div>
      {where && <div className="mt-2 text-xs text-gray">{where}</div>}
      {info?.known && <div className="mt-1 text-xs text-gray/70">{t('spec.noDisk')}</div>}
    </div>
  )
}
