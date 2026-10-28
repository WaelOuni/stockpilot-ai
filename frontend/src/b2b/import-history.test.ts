import { afterEach, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ImportHistoryEntry from './ImportHistoryEntry.vue'
import { previewFixture } from './fixtures'
import { language } from './preferences'
import type { Preview } from './contracts'

afterEach(() => { language.value = 'fr' })
const modes = [
  ['stock_only', 'Stock physique uniquement', 'Physical stock only'],
  ['metadata_only', 'Descriptions et prix uniquement', 'Descriptions and prices only'],
  ['create_only', 'Nouveaux produits uniquement', 'New products only'],
] as const
const statuses = [
  ['preview', 'Aperçu enregistré — non appliqué', 'Preview saved — not applied'],
  ['invalid', 'Fichier invalide', 'Invalid file'],
  ['applied', 'Import appliqué', 'Import applied'],
  ['rejected', 'Import refusé', 'Import rejected'],
] as const

for (const [mode, frMode, enMode] of modes) {
  for (const [status, frStatus, enStatus] of statuses) {
    it(`translates ${mode}/${status} while preserving full references in collapsed details`, async () => {
      language.value = 'fr'
      const entry = { ...previewFixture, mode, status, import_id: 'full-import-id-123456789', file_fingerprint: 'a'.repeat(64) } as Preview
      const wrapper = mount(ImportHistoryEntry, { props: { entry } })
      const primary = () => wrapper.findAll('li > p').map(p => p.text()).join(' ')
      expect(primary()).toContain(frMode)
      expect(primary()).toContain(frStatus)
      expect(primary()).not.toContain(entry.import_id)
      expect(primary()).not.toContain(mode)
      const details = wrapper.get('details')
      expect(details.attributes('open')).toBeUndefined()
      expect(details.text()).toContain(entry.import_id)
      expect(details.text()).toContain(entry.file_fingerprint)
      expect(details.text()).toContain(`${mode} · ${status}`)
      language.value = 'en'; await nextTick()
      expect(primary()).toContain(enMode)
      expect(primary()).toContain(enStatus)
      expect(wrapper.get('summary').text()).toBe('References and details')
      wrapper.unmount()
    })
  }
}

it('renders historical identifiers as text, without interpreting markup', () => {
  const entry = { ...previewFixture, import_id: '<script>alert(1)</script>' } as Preview
  const wrapper = mount(ImportHistoryEntry, { props: { entry } })
  expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.get('details').text()).toContain(entry.import_id)
  wrapper.unmount()
})
