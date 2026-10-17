import { createHmac } from 'node:crypto'
import { expect, test } from '@playwright/test'

function token(customer: string) {
  const encoded = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url')
  const body = `${encoded({ alg: 'HS256', typ: 'JWT' })}.${encoded({ customer_id: customer, exp: Math.floor(Date.now() / 1000) + 600 })}`
  return `${body}.${createHmac('sha256', process.env.CUSTOMER_JWT_SECRET!).update(body).digest('base64url')}`
}

test('synthetic API → real graph → Vue: search, followups, target, comparison, isolation and clearing', async ({ page }) => {
  await page.goto('/assistant')
  async function connect(customer: string) {
    const input = page.getByLabel('JWT client', { exact: true })
    if (!await input.isVisible()) await page.locator('details.token-panel > summary').click()
    await input.fill(token(customer))
    await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  }
  await connect('synthetic-a')
  async function send(message: string) {
    const received = page.waitForResponse(r => r.url().endsWith('/api/assistant') && r.request().method() === 'POST')
    await page.getByLabel('Votre message', { exact: true }).fill(message)
    await page.getByRole('button', { name: 'Envoyer', exact: true }).click()
    const response = await received
    expect(response.status()).toBe(200)
    const answer = await response.json()
    await expect(page.getByRole('log')).toContainText(answer.response)
    return answer
  }
  const search = await send('Je cherche une perceuse disponible à moins de 100 euros.')
  expect(search.recommendations.map((p: { sku: string }) => p.sku)).toEqual(['D-A', 'D-B', 'D-C'])
  await expect(page.getByRole('article', { name: 'Produit recommandé', exact: true })).toHaveCount(3)
  const raised = await send('Et si je monte le budget à 150 euros ?')
  expect(raised.budget).toBe(150)
  expect(raised.last_advice_criteria.available).toBe(true)
  const two = await send('Montre-moi seulement deux produits.')
  expect(two.recommendations).toHaveLength(2)
  await expect(page.getByRole('article', { name: 'Produit recommandé', exact: true })).toHaveCount(2)
  await send('Conseille-moi sur la Perceuse Concurrence Synthétique V017.')
  await expect(page.getByRole('article', { name: 'Produit recommandé', exact: true })).toHaveCount(1)
  const compare = await send('Compare cette perceuse avec deux autres disponibles.')
  expect(compare.recommendations.map((p: { sku: string }) => p.sku)).toEqual(['D-C', 'D-A', 'D-B'])
  await expect(page.getByRole('article', { name: 'Produit de référence', exact: true })).toHaveCount(1)
  await expect(page.getByRole('article', { name: 'Alternative', exact: true })).toHaveCount(2)
  const clarification = await send('Compare cette perceuse avec deux autres.')
  expect(clarification.advice_status).toBe('CLARIFICATION_REQUIRED')
  await expect(page.locator('article.recommendation')).toHaveCount(0)
  expect((await send('Je cherche une perceuse à moins de 1 euro.')).advice_status).toBe('NO_RESULTS')
  expect((await send('Conseille-moi sur le SKU OFFLINE-1')).advice_status).toBe('CATALOGUE_UNAVAILABLE')
  await send('Conseille-moi sur le SKU D-C')
  await send('Le stock du SKU D-C ?')
  await expect(page.locator('article.recommendation')).toHaveCount(0)
  await send('Conseille-moi sur le SKU D-C')
  await page.getByRole('button', { name: 'Nouvelle conversation', exact: true }).click()
  expect((await send('Compare cette perceuse avec deux autres.')).advice_status).toBe('CLARIFICATION_REQUIRED')
  await send('Conseille-moi sur le SKU D-C')
  await connect('synthetic-b')
  expect((await send('Compare cette perceuse avec deux autres.')).advice_status).toBe('CLARIFICATION_REQUIRED')
  await expect(page.getByRole('alert')).toHaveCount(0)
})
