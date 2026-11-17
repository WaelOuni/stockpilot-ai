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

for (const language of ['fr', 'en'] as const) for (const theme of ['light', 'dark'] as const) {
  test(`${language}/${theme}: membership refusal explains both recovery paths without data`, async ({ page }) => {
    await page.addInitScript(({ language, theme }) => {
      localStorage.setItem('stockpilot.preferences.v1', JSON.stringify({ language, theme }))
    }, { language, theme })
    const calls: string[] = []
    await page.route(/\/b2b\/(auth|api)\//, async route => {
      calls.push(new URL(route.request().url()).pathname)
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' })
    })
    await page.goto('/b2b-app/?login=denied&organization_id=PRIVATE&return_to=https://evil.example')
    const code = page.getByLabel(language === 'fr' ? 'Code d’entreprise' : 'Company code')
    await expect(code).toBeFocused()
    await expect(page).toHaveURL('/b2b-app/')
    await expect(page.getByRole('alert')).toContainText(language === 'fr' ? 'Ce compte n’a pas accès' : 'This account cannot access')
    await expect(page.locator('body')).not.toContainText('PRIVATE')
    const switchAccount = page.getByRole('button', { name: language === 'fr' ? 'Se connecter avec un autre compte' : 'Sign in with another account' })
    await expect(switchAccount).toBeDisabled()
    await code.fill('fixture-org')
    await expect(switchAccount).toBeEnabled()
    await page.screenshot({ path: `test-results/auth-denied-${language}-${theme}.png`, fullPage: true })
    const choose = page.getByRole('button', { name: language === 'fr' ? 'Choisir une autre organisation' : 'Choose another organization' })
    await choose.focus(); await page.keyboard.press('Enter')
    await expect(code).toBeFocused(); await expect(code).toHaveValue('')
    await expect(page.getByRole('alert')).toHaveCount(0)
    expect(calls).toEqual(['/b2b/auth/session'])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
