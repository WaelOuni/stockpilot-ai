import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

const root = resolve('..')
const python = process.env.E2E_PYTHON || resolve(root, '.venv/bin/python')
function product(sku: string) {
  return JSON.parse(execFileSync(python, [
    resolve(root, 'frontend/e2e/support/backend_probe.py'), 'product', 'import-reference', sku,
  ], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }))
}
test('isolated CREATE_ONLY CSV import preview confirm Mongo zero stock catalogue and conflict', async ({ page }) => {
  if (process.env.MONGO_DATABASE !== 'stockpilot_e2e'
    || !process.env.E2E_ADMIN_USERNAME || !process.env.E2E_ADMIN_PASSWORD) throw new Error('Isolated launcher required.')
  await page.goto('/admin/products/import')
  await page.getByLabel('Identifiant administrateur').fill(process.env.E2E_ADMIN_USERNAME)
  await page.getByLabel('Mot de passe administrateur').fill(process.env.E2E_ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Commandes administrateur', exact: true })).toBeVisible()
  await page.goto('/admin/products')
  await page.getByRole('link', { name: 'Importer des produits CSV', exact: true }).click()
  const baseline = await page.evaluate(async () => (await (await fetch('/api/backoffice/dashboard')).json()).product_stats)
  const csv = 'sku;name;category;price\nE2E-IMPORT-A;"Import perceuse; test";DRILL;10.5\nE2E-IMPORT-B;Import scie;SAW;20\n'
  await page.getByLabel('Fichier CSV').setInputFiles({ name: 'import.csv', mimeType: 'text/plain', buffer: Buffer.from(csv) })
  const previewResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/backoffice/products/import/preview') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Prévisualiser', exact: true }).click()
  const approved = await (await previewResponse).json()
  await expect(page.getByRole('heading', { name: 'Aperçu', exact: true })).toBeVisible()
  await expect(page.getByText('Analysés : 2 · Valides : 2 · Invalides : 0 · SKU existants : 0')).toBeVisible()
  const before = await page.evaluate(async () => (await (await fetch('/api/backoffice/dashboard')).json()).product_stats)
  expect(before).toEqual(baseline)
  await expect(page.getByRole('button', { name: 'Confirmer l’import' })).toBeDisabled()
  await page.getByLabel('Je confirme la création de tous ces nouveaux produits sans mise à jour.').check()
  const executionResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/backoffice/products/import/execute') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Confirmer l’import' }).click()
  const executed = await executionResponse
  expect(executed.status()).toBe(200)
  const receipt = await executed.json()
  await expect(page.getByRole('heading', { name: 'Import confirmé par le serveur' })).toBeVisible()
  expect(product('E2E-IMPORT-A')).toMatchObject({ sku: 'E2E-IMPORT-A', stock: 0, name: 'Import perceuse; test', price: 10.5 })
  expect(product('E2E-IMPORT-B')).toMatchObject({ sku: 'E2E-IMPORT-B', stock: 0, category: 'SAW' })
  const reconciled = await page.evaluate(async (operation) =>
    (await fetch(`/api/backoffice/products/import/batches/${operation}`, { credentials: 'include' })).json(),
  receipt.operation_id)
  expect(reconciled).toEqual(receipt)
  const replay = await page.evaluate(async ({ csv, operation, binding }) => {
    const session = await (await fetch('/api/backoffice/session/me')).json()
    const response = await fetch('/api/backoffice/products/import/execute', {
      method: 'POST', credentials: 'include', body: csv,
      headers: { 'Content-Type': 'text/csv', 'X-Admin-CSRF': session.csrf_token,
        'X-Import-Operation-Id': operation, 'X-Import-Preview': binding, 'X-Import-Confirm': 'CREATE_ONLY' },
    })
    return { status: response.status, body: await response.json() }
  }, { csv, operation: receipt.operation_id, binding: approved.binding })
  expect(replay).toEqual({ status: 200, body: receipt })
  const after = await page.evaluate(async () => (await (await fetch('/api/backoffice/dashboard')).json()).product_stats)
  expect(after.total_products).toBe(baseline.total_products + 2)
  expect(after.total_stock_units).toBe(baseline.total_stock_units)
  await page.getByRole('link', { name: 'Consulter le catalogue', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Import perceuse; test', exact: true })).toBeVisible()
  await page.goto('/products?q=E2E-IMPORT')
  await expect(page.getByRole('link', { name: /Import perceuse; test/ }).first()).toBeVisible()
  await page.goto('/admin/products/import')
  await page.getByLabel('Fichier CSV').setInputFiles({ name: 'again.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) })
  await page.getByRole('button', { name: 'Prévisualiser', exact: true }).click()
  await expect(page.getByText('Analysés : 2 · Valides : 0 · Invalides : 0 · SKU existants : 2')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Confirmer l’import' })).toBeDisabled()
  const unchanged = await page.evaluate(async () => (await (await fetch('/api/backoffice/dashboard')).json()).product_stats)
  expect(unchanged).toEqual(after)
})
