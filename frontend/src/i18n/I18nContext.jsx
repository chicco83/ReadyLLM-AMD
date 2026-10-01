import { createContext, useContext, useState, useEffect } from 'react'
import { translations } from './translations'

const I18nContext = createContext(null)
const STORAGE_KEY = 'readyllm_lang'

// [v1.1.0 2026-10-01] Lingue supportate: en / it / zh (aggiunto l'italiano).
// Ordine di rotazione del pulsante di cambio lingua: en -> it -> zh -> en
const LANGS = ['en', 'it', 'zh']
const LANG_LABEL = { en: 'English', it: 'Italiano', zh: '中文' }
function nextLang(l) {
  return LANGS[(LANGS.indexOf(l) + 1) % LANGS.length]
}

export function I18nProvider({ children }) {
  const [lang, setLang] = useState(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      // Versione precedente (2026-10-01): if (saved === 'en' || saved === 'zh') return saved
      if (LANGS.includes(saved)) return saved
    } catch { /* ignore */ }
    return 'it' // lingua predefinita: italiano (v1.1.0; prima 'en' — le lingue supportate sono ora en / it / zh)
  })

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, lang) } catch { /* ignore */ }
  }, [lang])

  // t(key, vars): restituisce il testo nella lingua corrente, se manca ripiega sull'inglese, poi sulla chiave stessa;
  // supporta l'interpolazione {var}, es. t('deploy.modelCount', { n: 5 })
  const t = (key, vars) => {
    const dict = translations[lang] || translations.en
    let s = dict[key] ?? translations.en[key] ?? key
    if (vars) {
      for (const [k, v] of Object.entries(vars)) {
        s = s.split(`{${k}}`).join(String(v))
      }
    }
    return s
  }

  // Versione precedente (2026-10-01): const toggle = () => setLang(l => (l === 'en' ? 'zh' : 'en'))
  const toggle = () => setLang(l => nextLang(l))

  return (
    <I18nContext.Provider value={{ lang, setLang, toggle, t }}>
      {children}
    </I18nContext.Provider>
  )
}

export function useI18n() {
  return useContext(I18nContext)
}

// Selettore di lingua (in fondo alla barra laterale)
export function LangSwitch() {
  const { lang, toggle } = useI18n()
  return (
    <button
      onClick={toggle}
      className="mt-3 w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm border border-white/10 text-fg/70 hover:text-fg hover:bg-white/[0.04] transition"
      title={`Language: ${LANG_LABEL[lang]} → ${LANG_LABEL[nextLang(lang)]}`}
    >
      <span>🌐</span>
      <span>{LANG_LABEL[lang]}</span>
    </button>
  )
}
