import { createHmac } from 'node:crypto'
import { expect, test } from '@playwright/test'

function token(customer: string) {
  const encoded = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url')
  const body = `${encoded({ alg: 'HS256', typ: 'JWT' })}.${encoded({ customer_id: customer, exp: Math.floor(Date.now() / 1000) + 600 })}`
  return `${body}.${createHmac('sha256', process.env.CUSTOMER_JWT_SECRET!).update(body).digest('base64url')}`
}

test('synthetic API → real graph → Vue: Advice snapshots, followups, comparisons and isolation', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.name))
  await page.goto('/assistant')
  async function connect(customer: string) {
    const input = page.getByLabel('JWT client', { exact: true })
    if (!await input.isVisible()) await page.locator('details.token-panel > summary').click()
    await input.fill(token(customer))
    await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  }
  await connect('synthetic-a')
  async function send(message: string) {
    const previous = await page.locator('.message.assistant').count()
    const received = page.waitForResponse(r => r.url().endsWith('/api/assistant') && r.request().method() === 'POST')
    await page.getByLabel('Votre message', { exact: true }).fill(message)
    await page.getByRole('button', { name: 'Envoyer', exact: true }).click()
    const response = await received
    expect(response.status()).toBe(200)
    const answer = await response.json()
    await expect(page.locator('.message.assistant')).toHaveCount(previous + 1)
    await expect(page.locator('.message.assistant').last()).toContainText(answer.response)
    return answer
  }
  const search = await send('Je cherche une perceuse disponible à moins de 100 euros.')
  expect(search.recommendations.map((p: { sku: string }) => p.sku)).toEqual(['D-A', 'D-B', 'D-C'])
  await expect(page.getByRole('article', { name: 'Produit recommandé', exact: true })).toHaveCount(3)
  const log = page.getByRole('log')
  await expect.poll(() => log.evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(64)
  await log.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')) })
  const raised = await send('Et si je monte le budget à 150 euros ?')
  expect(raised.budget).toBe(150)
  expect(raised.last_advice_criteria.available).toBe(true)
  expect(await log.evaluate(element => element.scrollTop)).toBe(0)
  await page.getByRole('button', { name: 'Voir la dernière réponse', exact: true }).click()
  await expect.poll(() => log.evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(64)
  const two = await send('Montre-moi seulement deux produits.')
  expect(two.recommendations).toHaveLength(2)
  await expect.poll(() => log.evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(64)
  await expect(page.locator('.message.assistant').last().locator('article.recommendation')).toHaveCount(2)
  await expect(page.getByRole('article', { name: 'Produit recommandé', exact: true })).toHaveCount(8)
  await send('Conseille-moi sur la Perceuse Concurrence Synthétique V017.')
  await expect(page.locator('.message.assistant').last().locator('article.recommendation')).toHaveCount(1)
  await expect(page.getByRole('article', { name: 'Produit recommandé', exact: true })).toHaveCount(9)
  const compare = await send('Compare cette perceuse avec deux autres disponibles.')
  expect(compare.recommendations.map((p: { sku: string }) => p.sku)).toEqual(['D-C', 'D-A', 'D-B'])
  await expect(page.getByRole('article', { name: 'Produit de référence', exact: true })).toHaveCount(1)
  await expect(page.getByRole('article', { name: 'Alternative', exact: true })).toHaveCount(2)
  const clarification = await send('Compare cette perceuse avec deux autres.')
  expect(clarification.advice_status).toBe('CLARIFICATION_REQUIRED')
  await expect(page.locator('.message.assistant').last().locator('article.recommendation')).toHaveCount(0)
  await expect(page.locator('article.recommendation')).toHaveCount(12)
  expect((await send('Je cherche une perceuse à moins de 1 euro.')).advice_status).toBe('NO_RESULTS')
  expect((await send('Conseille-moi sur le SKU OFFLINE-1')).advice_status).toBe('CATALOGUE_UNAVAILABLE')
  await send('Conseille-moi sur le SKU D-C')
  await send('Le stock du SKU D-C ?')
  await expect(page.locator('.message.assistant').last().locator('article.recommendation')).toHaveCount(0)
  await expect(page.locator('article.recommendation')).toHaveCount(13)
  await expect(page.locator('.snapshot-notice').first()).toContainText('non actualisés')
  await page.locator('.message.assistant').nth(3).scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('advice-history-desktop.png'),
    mask: [page.locator('details.token-panel')] })
  await send('Conseille-moi sur le SKU D-C')
  await page.getByRole('button', { name: 'Nouvelle conversation', exact: true }).click()
  await expect(page.locator('article.recommendation')).toHaveCount(0)
  expect((await send('Compare cette perceuse avec deux autres.')).advice_status).toBe('CLARIFICATION_REQUIRED')
  await send('Conseille-moi sur le SKU D-C')
  await connect('synthetic-b')
  await expect(page.locator('article.recommendation')).toHaveCount(0)
  expect((await send('Compare cette perceuse avec deux autres.')).advice_status).toBe('CLARIFICATION_REQUIRED')
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(errors).toEqual([])
})

test('narrow screen: long untrusted content stays text, with readable facts and no overflow', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 360, height: 800 })
  const name = '<img src=x onerror=alert(1)> ' + 'DénominationTechniqueTrèsLongue'.repeat(12)
  await page.route('**/api/assistant', route => route.fulfill({ json: {
    intent: 'PRODUCT_ADVICE', advice_status: 'RESULTS', response: 'Voici la fiche du produit demandé.',
    recommendations: [{ sku: 'SYNTHETIC-LONG', product_name: name, category: 'DRILL',
      price: 1234.567, stock: 0, explanation: '<script>window.untrusted=true</script>' }],
  } }))
  await page.goto('/assistant')
  await page.getByLabel('JWT client', { exact: true }).fill(token('synthetic-responsive'))
  await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  await page.getByLabel('Votre message', { exact: true }).fill('Fiche du produit synthétique')
  await page.getByRole('button', { name: 'Envoyer', exact: true }).click()
  const card = page.getByRole('article', { name: 'Produit recommandé', exact: true })
  await expect(card).toContainText('1\u202f234,567')
  await expect(card).toContainText('Indisponible')
  await expect(card).toContainText('Perceuse')
  await expect(card.locator('img, script')).toHaveCount(0)
  await card.locator('summary').click()
  await expect(card).toContainText('<script>window.untrusted=true</script>')
  expect(await page.evaluate(() => Object.hasOwn(window, 'untrusted'))).toBe(false)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(await card.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  await expect(card).not.toContainText('€')
  await card.scrollIntoViewIfNeeded()
  await page.getByRole('log').evaluate(element => { element.scrollTop = 0 })
  await page.screenshot({ path: testInfo.outputPath('advice-mobile-long-name.png'),
    mask: [page.locator('details.token-panel')] })
})

test('historical ORDER controls cannot confirm a later preparation or reactivate after a decision', async ({ page }) => {
  let calls = 0
  const pending = { intent: 'ORDER', response: 'Une commande attend votre décision.', order_status: 'PENDING_CONFIRMATION',
    pending_order: { operation_id: 'synthetic-op', product_name: 'Produit synthétique', quantity: 1, status: 'PENDING_CONFIRMATION' } }
  await page.route('**/api/assistant', async route => {
    const { message } = route.request().postDataJSON()
    calls++
    await route.fulfill({ json: message === 'Oui je confirme' ? { intent: 'ORDER', response: 'Commande confirmée.',
      confirmation_handled: true, order_status: 'CONFIRMED', created_order_id: 'synthetic-order' } : pending })
  })
  await page.goto('/assistant')
  await page.getByLabel('JWT client', { exact: true }).fill(token('synthetic-order-controls'))
  await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  for (const [index, message] of ['Préparer la première commande', 'Préparer une nouvelle commande'].entries()) {
    await page.getByLabel('Votre message', { exact: true }).fill(message)
    await page.getByRole('button', { name: 'Envoyer', exact: true }).click()
    await expect(page.locator('.confirmation-card')).toHaveCount(index + 1)
    await expect(page.getByLabel('Votre message', { exact: true })).toBeEnabled()
  }
  const old = page.locator('.confirmation-card').first()
  const current = page.locator('.confirmation-card').last()
  await expect(old.getByRole('button', { name: 'Confirmer', exact: true })).toBeDisabled()
  await expect(old.getByRole('button', { name: 'Annuler', exact: true })).toBeDisabled()
  await current.getByRole('button', { name: 'Confirmer', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Commande créée', exact: true })).toBeVisible()
  for (const button of await page.locator('.confirmation-card button').all()) await expect(button).toBeDisabled()
  expect(calls).toBe(3)
})
