import { createHmac } from 'node:crypto'
import { expect, test } from '@playwright/test'

const cases = [
  { message: 'Rechercher trois perceuses disponibles sous 100', skus: ['D-A', 'D-B', 'D-C'], budget: 100, available: true, limit: 3 },
  { message: 'Chercher trois perceuses à moins de 100', skus: ['D-O', 'D-A', 'D-B'], budget: 100, available: null, limit: 3 },
  { message: 'Recherche de perceuses disponibles sous 100', skus: ['D-A', 'D-B', 'D-C'], budget: 100, available: true, limit: 3 },
  { message: 'Trouve-moi trois perceuses sous 100 €', skus: ['D-O', 'D-A', 'D-B'], budget: 100, available: null, limit: 3 },
  { message: 'Je cherche trois perceuses disponibles à moins de 100 euros', skus: ['D-A', 'D-B', 'D-C'], budget: 100, available: true, limit: 3 },
  { message: 'Montre-moi les perceuses disponibles pour moins de 100 €', skus: ['D-A', 'D-B', 'D-C'], budget: 100, available: true, limit: 3 },
  { message: 'Propose-moi deux visseuses en stock à moins de 80 €', skus: ['S-A'], budget: 80, available: true, limit: 2 },
  { message: 'Quelles scies sont disponibles sous 150 € ?', skus: ['W-A'], budget: 150, available: true, limit: 3 },
]

function token(customer: string) {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url')
  const body = `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ customer_id: customer, exp: Math.floor(Date.now() / 1000) + 600 })}`
  return `${body}.${createHmac('sha256', process.env.CUSTOMER_JWT_SECRET!).update(body).digest('base64url')}`
}

// The dedicated harness starts its own API/Vite, in-memory catalogue/checkpoints
// and a classifier returning UNKNOWN for searches. No DEMO, database or Ollama.
for (const [index, row] of cases.entries()) {
  test(`natural catalogue search: ${row.message}`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.name))
    await page.goto('/assistant')
    await page.getByLabel('JWT client', { exact: true }).fill(token(`synthetic-language-${index}`))
    await page.getByRole('button', { name: 'Connecter', exact: true }).click()
    const received = page.waitForResponse(r => r.url().endsWith('/api/assistant') && r.request().method() === 'POST')
    await page.getByLabel('Votre message', { exact: true }).fill(row.message)
    await page.getByRole('button', { name: 'Envoyer', exact: true }).click()
    const response = await received
    expect(response.status()).toBe(200)
    const answer = await response.json()
    expect(answer.intent).toBe('PRODUCT_ADVICE')
    expect(answer.recommendations.map((p: { sku: string }) => p.sku)).toEqual(row.skus)
    expect(answer.last_advice_criteria).toMatchObject({ maximum: row.budget, exclusive_maximum: true,
      available: row.available, limit: row.limit })
    for (const product of answer.recommendations) {
      expect(product.price).toBeLessThan(row.budget)
      if (row.available) expect(product.stock).toBeGreaterThan(0)
    }
    const last = page.locator('.message.assistant').last()
    await expect(last.locator('article.recommendation')).toHaveCount(row.skus.length)
    for (const sku of row.skus) await expect(last).toContainText(sku)
    await expect(last).toContainText(answer.response)
    await expect(page.locator('.confirmation-card')).toHaveCount(0)
    expect(errors).toEqual([])
  })
}
