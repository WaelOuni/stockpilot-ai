import { ref, watch } from 'vue'
import { english } from './translations'

const key = 'stockpilot.preferences.v1'
type Preferences = { language: 'fr' | 'en'; theme: 'light' | 'dark' }
function initial(): Preferences {
  const defaults: Preferences = { language: 'fr', theme: typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light' }
  try {
    const data = JSON.parse(localStorage.getItem(key) ?? '{}')
    return { language: data.language === 'en' ? 'en' : 'fr', theme: ['light', 'dark'].includes(data.theme) ? data.theme : defaults.theme }
  } catch { return defaults }
}
const defaults = initial()
export const language = ref<'fr' | 'en'>(defaults.language)
export const theme = ref<'light' | 'dark'>(defaults.theme)
export function t(source: string): string {
  if (language.value === 'fr') return source
  if (english[source]) return english[source]
  // Application notices can append a reference or a bounded server outcome code.
  for (const [fr, en] of Object.entries(english)) {
    if (fr.length > 15 && fr.endsWith(' :') && source.startsWith(fr + ' ')) return en + source.slice(fr.length)
  }
  const count = /^(\d+) (ligne\(s\) exportée\(s\)\.)$/.exec(source)
  if (count) return count[1] + ' ' + english[count[2]!]
  return source
}
export function dateLabel(value: string) { return new Date(value).toLocaleString(language.value === 'en' ? 'en-GB' : 'fr-FR') }
function apply() {
  document.documentElement.lang = language.value
  document.documentElement.dataset.theme = theme.value
  document.documentElement.style.colorScheme = theme.value
  try { localStorage.setItem(key, JSON.stringify({ language: language.value, theme: theme.value })) } catch { /* Preferences stay usable in memory. */ }
}
watch([language, theme], apply)
export function applyPreferences() { apply() }
