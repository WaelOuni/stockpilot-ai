import { expect, test } from '@playwright/test'

test('isolated read-only admin session: login, refresh, pagination, errors, logout', async ({ page, request }) => {
  const username = process.env.E2E_ADMIN_USERNAME
  const password = process.env.E2E_ADMIN_PASSWORD
  if (!username || !password || process.env.MONGO_DATABASE !== 'stockpilot_e2e') {
    throw new Error('Isolated admin launcher configuration is missing.')
  }
  // No credential-bearing assertions, traces, screenshots, video or console capture.
  await page.goto('/admin/orders')
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  await expect(page).toHaveURL(/\/admin\/login/)
  await page.getByLabel('Identifiant administrateur').fill(username)
  await page.getByLabel('Mot de passe administrateur').fill(password)
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Commandes administrateur', exact: true })).toBeVisible()
  await expect(page.getByText('Aucune commande sur cette page.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Suivant', exact: true })).toBeDisabled()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Commandes administrateur', exact: true })).toBeVisible()

  const access = await page.evaluate(async () => {
    const me = await fetch('/api/backoffice/session/me', { credentials: 'include' })
    const session = await me.json() as { csrf_token: string }
    const list = await fetch('/api/backoffice/orders', { credentials: 'include' })
    const badLogout = await fetch('/api/backoffice/session/logout', {
      method: 'POST', credentials: 'include', headers: { 'X-Admin-CSRF': 'wrong' },
    })
    const after = await fetch('/api/backoffice/session/me', { credentials: 'include' })
    const mutation = await fetch('/api/orders/nonexistent/cancel', { method: 'POST', credentials: 'include' })
    const customer = await fetch('/api/orders', { credentials: 'include' })
    const cookiesInvisible = !document.cookie.includes('stockpilot_admin')
    const csrfPresent = typeof session.csrf_token === 'string' && session.csrf_token.length > 20
    return { me: me.status, list: list.status, badLogout: badLogout.status, after: after.status,
      mutation: mutation.status, customer: customer.status, cookiesInvisible, csrfPresent,
      cache: list.headers.get('cache-control') }
  })
  expect(access).toEqual({ me: 200, list: 200, badLogout: 403, after: 200,
    mutation: 401, customer: 401, cookiesInvisible: true, csrfPresent: true, cache: 'no-store' })
  const unauthenticated = await request.get('/api/backoffice/orders')
  expect(unauthenticated.status()).toBe(401)
  const evil = await request.post('/api/backoffice/session/login', {
    headers: { Origin: 'https://evil.test', 'X-Admin-CSRF': 'login' },
    data: { username: 'not-a-secret', password: 'not-a-secret-password' },
  })
  expect(evil.status()).toBe(403)
  const technical = await request.get('/api/backoffice/orders', {
    headers: { Authorization: `Bearer ${process.env.E2E_BACKOFFICE_API_TOKEN}` },
  })
  expect(technical.status()).toBe(200)
  expect((await technical.json()).count).toBe(0)
  await page.goto('/admin/orders?offset=20')
  await expect(page.getByText('Aucune commande sur cette page.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Précédent', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Précédent', exact: true }).click()
  await expect(page).toHaveURL(/\/admin\/orders$/)
  await page.goto('/admin/orders/nonexistent')
  await expect(page.getByRole('alert')).toContainText('Commande introuvable')
  await expect(page.getByRole('button', { name: /Annuler|Confirmer/ })).toHaveCount(0)
  await page.getByRole('link', { name: 'Retour aux commandes administrateur' }).click()
  await page.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  const loggedOut = await page.evaluate(async () =>
    (await fetch('/api/backoffice/session/me', { credentials: 'include' })).status)
  expect(loggedOut).toBe(401)
  await page.goto('/admin/orders')
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  // No orders created or changed; deterministic seed remains untouched.
  const products = await request.get('/api/products')
  const rows = (await products.json()).items as Array<{ sku: string; stock: number }>
  expect(rows.map(({ sku, stock }) => ({ sku, stock }))).toEqual([
    { sku: 'E2E-BOSCH-S500', stock: 5 },
    { sku: 'E2E-BOSCH-X100', stock: 10 },
    { sku: 'E2E-MAKITA-D200', stock: 0 },
  ])
})
