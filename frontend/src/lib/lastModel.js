// Ultimo modello selezionato per macchina target, condiviso tra Deploy e Tuning (v1.1.11, 2026-10-01).
// Il modello scelto in Deploy compare da solo nel Tuning e viceversa: entrambe le schede leggono/scrivono qui.
const LAST_MODEL_KEY = 'readyllm:lastModel'

export function readLastModel(targetId) {
  try {
    const m = JSON.parse(localStorage.getItem(LAST_MODEL_KEY) || '{}')
    return m[targetId] || ''
  } catch { return '' }
}

export function writeLastModel(targetId, model) {
  try {
    const m = JSON.parse(localStorage.getItem(LAST_MODEL_KEY) || '{}')
    m[targetId] = model
    localStorage.setItem(LAST_MODEL_KEY, JSON.stringify(m))
  } catch { /* errori di scrittura (es. modalita' privata) ignorabili */ }
}
