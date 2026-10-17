import { expect, test } from '@playwright/test'

test('real E2E catalogue advice, exact facts/links, absence and clarification without seed', async ({ page, request }) => {
  if (process.env.MONGO_DATABASE !== 'stockpilot_e2e' || !process.env.ADVICE_E2E_TOKEN) throw new Error('Isolated advice harness required.')
  await page.goto('/assistant')
  await page.getByLabel('JWT client', { exact: true }).fill(process.env.ADVICE_E2E_TOKEN)
  await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  const headers = { Authorization: `Bearer ${process.env.ADVICE_E2E_TOKEN}` }
  const catalogue = await (await request.get('/api/products?limit=100', { headers })).json()
  expect(catalogue.items.length).toBeGreaterThan(0)
  const reference = catalogue.items[0]
  const categoryNames: Record<string, string> = { DRILL: 'perceuses', SAW: 'scies', SANDER: 'ponceuses', SCREWDRIVER: 'visseuses', OTHER: 'produits' }
  const question = `Propose-moi trois ${categoryNames[reference.category]} avec un budget de ${Math.ceil(reference.price) + 1} euros.`
  async function send(message: string) {
    const received = page.waitForResponse(response => response.url().endsWith('/api/assistant') && response.request().method() === 'POST', { timeout: 180_000 })
    await page.getByLabel('Votre message', { exact: true }).fill(message)
    await page.getByRole('button', { name: 'Envoyer', exact: true }).click()
    const response = await received
    expect(response.status()).toBe(200)
    return response.json()
  }
  const answer = await send(question)
  expect(answer.intent).toBe('PRODUCT_ADVICE')
  expect(answer.product_name).toBeNull()
  expect(answer.budget).toBe(Math.ceil(reference.price) + 1)
  expect(answer.advice_status).toBe('RESULTS')
  console.log('V8_LLM_STATUS=' + (answer.response.includes('modèle est indisponible') ? 'fallback' : 'validated'))
  expect(answer.recommendations.length).toBeGreaterThan(0)
  expect(answer.recommendations.length).toBeLessThanOrEqual(3)
  for (const product of answer.recommendations) {
    const actual = await (await request.get(`/api/products/${encodeURIComponent(product.sku)}`, { headers })).json()
    expect(product.product_name).toBe(actual.product_name)
    expect(product.price).toBe(actual.price)
    expect(product.stock).toBe(actual.stock)
    if (reference.category !== 'OTHER') expect(product.category).toBe(reference.category)
    expect(product.price).toBeLessThanOrEqual(Math.ceil(reference.price) + 1)
    await expect(page.getByRole('link', { name: `Voir la fiche de ${product.product_name}`, exact: true })).toBeVisible()
  }
  const first = answer.recommendations[0]
  await page.getByRole('link', { name: `Voir la fiche de ${first.product_name}`, exact: true }).click()
  await expect(page.getByRole('heading', { name: first.product_name, exact: true })).toBeVisible()
  await page.goto('/assistant')
  await page.getByLabel('JWT client', { exact: true }).fill(process.env.ADVICE_E2E_TOKEN)
  await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  const clarification = await send('Propose-moi trois produits dans mon budget.')
  expect(clarification.advice_status).toBe('CLARIFICATION_REQUIRED')
  expect(clarification.recommendations).toEqual([])
  const empty = await send('Je cherche une perceuse à moins de 0,01 euro.')
  expect(empty.advice_status).toBe('NO_RESULTS')
  expect(empty.recommendations).toEqual([])
  await expect(page.getByRole('alert')).toHaveCount(0)
})
