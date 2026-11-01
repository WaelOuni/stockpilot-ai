import { test, expect } from '@playwright/test'

for (const language of ['fr', 'en'] as const) for (const outcome of ['cancelled', 'failed'] as const) {
  test(`${language}: ${outcome} returns to a blank, focused organization choice`, async ({ page }) => {
    await page.addInitScript(language => {
      localStorage.setItem('stockpilot.preferences.v1', JSON.stringify({ language, theme: 'light' }))
      sessionStorage.setItem('stockpilot.b2b.return.v1', JSON.stringify({ user: 'old-user', organization: 'old-org', value: '/b2b-app/orders' }))
    }, language)
    const calls: string[] = []
    await page.route(/\/b2b\/(auth|api)\//, async route => {
      calls.push(new URL(route.request().url()).pathname)
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{"code":"UNAUTHENTICATED"}' })
    })
    await page.goto(`/b2b-app/?login=${outcome}&return_to=https://evil.example&error_description=PRIVATE`)
    const organization = page.getByLabel(language === 'fr' ? 'Code d’entreprise' : 'Company code')
    await expect(organization).toBeFocused()
    await expect(organization).toHaveValue('')
    await expect(page).toHaveURL('/b2b-app/')
    await expect(page.getByRole('status')).toContainText(language === 'fr' ? (outcome === 'cancelled' ? 'Connexion annulée' : 'La connexion a échoué') : (outcome === 'cancelled' ? 'Login cancelled' : 'Login failed'))
    await expect(page.locator('body')).not.toContainText('PRIVATE')
    expect(calls).toEqual(['/b2b/auth/session'])
    expect(await page.evaluate(() => sessionStorage.length)).toBe(0)
    await expect(page.getByRole('button', { name: language === 'fr' ? 'Continuer avec Keycloak' : 'Continue with Keycloak' })).toBeDisabled()
  })
}
