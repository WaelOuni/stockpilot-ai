import { expect, test } from '@playwright/test'
import { sessionFixture } from '../src/b2b/fixtures'

for (const scenario of [
  { language: 'fr', theme: 'light', width: 390 },
  { language: 'en', theme: 'dark', width: 390 },
  { language: 'fr', theme: 'dark', width: 1280 },
  { language: 'en', theme: 'light', width: 1280 },
] as const) {
  test('contact ' + scenario.language + '/' + scenario.theme + '/' + scenario.width + ': local email and administrator flow', async ({ page }, info) => {
    const fr = scenario.language === 'fr'
    const label = (frText: string, enText: string) => fr ? frText : enText
    await page.setViewportSize({ width: scenario.width, height: 900 })
    await page.addInitScript(p => {
      localStorage.setItem('stockpilot.preferences.v1', JSON.stringify(p))
      Object.assign(window, { capturedMailto: [] as string[] })
      // Capture even detached anchors, and never open an OS email client in a test.
      const originalClick = HTMLAnchorElement.prototype.click
      HTMLAnchorElement.prototype.click = function () {
        if (this.protocol === 'mailto:') {
          ;(window as unknown as { capturedMailto: string[] }).capturedMailto.push(this.href)
        } else originalClick.call(this)
      }
    }, scenario)
    let admin = false
    const permissions = ['contact:create', 'contact:read_own']
    const rows = Array.from({ length: 23 }, (_, i) => ({
      request_id: i.toString(16).padStart(32, '0'), subject: 'Demande synthétique ' + i, category: 'access',
      description: '<img src=x onerror=alert(1)> reste du texte',
      created_by: 'user-a', created_at: '2026-10-21T10:00:00Z', updated_at: '2026-10-21T10:00:00Z', status: 'open', revision: 1,
    }))
    const replies: { reply_id: string; request_id: string; body: string; created_by: string; created_at: string }[] = []
    const writes: { path: string; body: Record<string, string> }[] = [], unexpected: string[] = []
    const counted = (items: unknown[], total: number, offset: number) => ({ items, total, offset, limit: 10, has_more: offset + 10 < total })
    await page.route(/\/b2b\/(?:auth|api)\//, async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname
      let body: unknown
      if (path === '/b2b/auth/session') body = {
        ...sessionFixture, user_id: admin ? 'admin-a' : 'user-a', role: admin ? 'ORGANIZATION_ADMIN' : 'READ_ONLY',
        assistant_available: true,
        permissions: [...sessionFixture.permissions, 'conversation:own', ...permissions, ...(admin ? ['contact:read_organization', 'contact:reply_organization', 'contact:manage_organization'] : [])],
      }
      else if (path.startsWith('/b2b/api/contact/requests')) {
        const id = path.split('/')[5], row = rows.find(r => r.request_id === id)
        if (req.method() === 'POST') {
          const input = req.postDataJSON() as Record<string, string>; writes.push({ path, body: input })
          if (!id) {
            const created = { ...rows[0]!, request_id: 'a'.repeat(32), subject: input.subject!, category: input.category!, description: input.description! }
            rows.unshift(created); body = { operation_id: input.operation_id, request_id: created.request_id, reply_id: null }
          } else if (row && path.endsWith('/replies')) {
            replies.push({ reply_id: 'b'.repeat(32), request_id: id, body: input.body!, created_by: 'admin-a', created_at: row.created_at })
            body = { operation_id: input.operation_id, request_id: id, reply_id: 'b'.repeat(32) }
          } else if (row && path.endsWith('/status')) {
            row.status = input.status!; body = { operation_id: input.operation_id, request_id: id, reply_id: null }
          } else unexpected.push(path)
        } else if (!id) {
          const offset = Number(url.searchParams.get('offset') ?? 0)
          const visible = rows.filter(r => (url.searchParams.get('scope') === 'organization' || r.created_by === (admin ? 'admin-a' : 'user-a'))
            && (!url.searchParams.get('status') || r.status === url.searchParams.get('status')))
          body = counted(visible.slice(offset, offset + 10), visible.length, offset)
        } else if (path.endsWith('/replies')) {
          const offset = Number(url.searchParams.get('offset') ?? 0), visible = replies.filter(r => r.request_id === id)
          body = counted(visible.slice(offset, offset + 10), visible.length, offset)
        } else body = row
      } else unexpected.push(path)
      await route.fulfill({ status: body ? 200 : 500, contentType: 'application/json', body: JSON.stringify(body ?? {}) })
    })
    await page.goto('/b2b-app/contact')
    const nav = page.getByRole('link', { name: label('Aide & contact', 'Help & contact'), exact: true })
    await nav.focus(); await page.keyboard.press('Enter')
    await expect(nav).toBeFocused()
    await expect(nav).toHaveAttribute('aria-current', 'page')
    await expect(page.getByRole('heading', { name: label('Aide & contact', 'Help & contact'), exact: true })).toBeVisible()
    const support = page.locator('.support-contact'), internal = page.locator('.organization-contact')
    await support.getByLabel(label('Objet obligatoire', 'Subject (required)')).fill('Écran & question ?')
    await support.getByLabel(label('Description obligatoire', 'Description (required)')).fill('Mon texte choisi, sans pièce jointe.')
    await support.getByRole('button', { name: label('Préparer mon email', 'Prepare my email'), exact: true }).click()
    await expect(support.getByRole('status')).toHaveText(label("Votre application de messagerie va s'ouvrir. Vérifiez puis envoyez votre email.", 'Your email application will open. Review and then send your email.'))
    const mailtos = await page.evaluate(() => (window as unknown as { capturedMailto: string[] }).capturedMailto)
    expect(mailtos).toHaveLength(1)
    const email = new URL(mailtos[0]!)
    expect(email.pathname).toBe('waelounie@gmail.com')
    expect(email.searchParams.get('body')).toBe('Mon texte choisi, sans pièce jointe.')
    expect(email.searchParams.get('subject')).toContain('Écran & question ?')
    expect(writes).toEqual([])
    await support.getByRole('button', { name: label('Copier le message', 'Copy message'), exact: true }).click()
    await expect(support.locator('textarea[readonly]')).toHaveValue(/Mon texte choisi/)
    const paging = internal.getByRole('navigation', { name: label('Pages des demandes', 'Request pages') })
    const last = paging.getByRole('button', { name: label('Dernière', 'Last'), exact: true })
    await expect(last).toBeEnabled(); await last.focus(); await page.keyboard.press('Enter')
    await expect(paging.locator('[aria-current=page]')).toHaveText('3')
    await expect(paging.getByRole('button', { name: label('Dernière', 'Last'), exact: true })).toBeDisabled()
    await internal.getByRole('button', { name: 'Demande synthétique 20', exact: true }).click()
    await expect(internal.locator('.plain-text')).toContainText('<img src=x onerror=alert(1)>')
    await expect(internal.locator('img,script')).toHaveCount(0)
    await expect(internal.getByRole('button', { name: label('Enregistrer la réponse', 'Save reply'), exact: true })).toHaveCount(0)
    await internal.getByLabel(label('Objet obligatoire', 'Subject (required)')).fill('Besoin accès synthétique')
    await internal.getByLabel(label('Description obligatoire', 'Description (required)')).fill('Question locale uniquement.')
    await internal.getByRole('button', { name: label('Enregistrer ma demande', 'Save my request'), exact: true }).click()
    await expect(internal.getByRole('heading', { name: 'Besoin accès synthétique', exact: true })).toBeVisible()
    expect(writes).toHaveLength(1)
    expect(Object.keys(writes[0]!.body).sort()).toEqual(['category', 'description', 'operation_id', 'subject'])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: info.outputPath('contact-user-' + scenario.language + '-' + scenario.width + '.png'), fullPage: true })

    admin = true; await page.reload()
    await expect(nav).toBeInViewport()
    await expect(internal.getByText(label('Aucune demande à afficher.', 'No requests to display.'), { exact: true })).toBeVisible()
    await internal.getByLabel(label('Périmètre des demandes', 'Request scope')).selectOption('organization')
    await internal.getByRole('button', { name: 'Besoin accès synthétique', exact: true }).click()
    await internal.getByLabel(label('Réponse obligatoire', 'Reply (required)')).fill('Réponse synthétique <b>en texte</b>')
    await internal.getByRole('button', { name: label('Enregistrer la réponse', 'Save reply'), exact: true }).click()
    await expect(internal.locator('.reply')).toContainText('Réponse synthétique <b>en texte</b>')
    await expect(internal.locator('.reply b')).toHaveCount(0)
    await internal.getByRole('button', { name: label('Marquer comme résolue', 'Mark as resolved'), exact: true }).click()
    await internal.getByRole('button', { name: label('Rouvrir la demande', 'Reopen request'), exact: true }).click()
    await expect(internal.getByRole('button', { name: label('Marquer comme résolue', 'Mark as resolved'), exact: true })).toBeEnabled()
    expect(writes).toHaveLength(4)
    expect(new Set(writes.map(w => w.body.operation_id)).size).toBe(4)
    expect(unexpected).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: info.outputPath('contact-admin-' + scenario.language + '-' + scenario.width + '.png'), fullPage: true })
  })
}
