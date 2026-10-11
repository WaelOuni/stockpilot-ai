import { test, expect } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

test('admin finishing: actions, alerts and long identifiers', async ({ page }) => {
  const stage = process.env.FINISH_STAGE || 'after'
  const output = resolve('../artifacts/ui-ux/finish', stage)
  mkdirSync(output, { recursive: true })
  await page.goto('/admin/login')
  await page.getByLabel('Identifiant administrateur').fill(process.env.E2E_ADMIN_USERNAME!)
  await page.getByLabel('Mot de passe administrateur').fill(process.env.E2E_ADMIN_PASSWORD!)
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page).toHaveURL(/admin\/orders/)
  const data = await page.evaluate(async () => ({
    product: (await (await fetch('/api/products?limit=1')).json()).items[0],
    order: (await (await fetch('/api/backoffice/orders?limit=1')).json()).items[0],
  }))
  const longSku = 'SKU-' + 'LONG-IDENTIFIER-'.repeat(10)
  const product = { ...data.product, sku: longSku }
  const order = { ...data.order, events: data.order.events.filter((event: { status: string }) => ['CREATED', 'PROCESSING'].includes(event.status)), customer_id: 'customer-' + 'long-'.repeat(35), status: 'PROCESSING' }
  // Synthetic responses validate extremes without database mutations; login is real.
  await page.route('**/api/backoffice/products/**', async route => {
    if (new URL(route.request().url()).pathname.endsWith('/stock-movements')) await route.fulfill({ json: { items: [], count: 0, total: 0, limit: 20, offset: 0, has_more: false } })
    else await route.fulfill({ json: product })
  })
  await page.route('**/api/backoffice/orders/*', route => route.fulfill({ json: order }))
  const screens = [
    ['edit-product', `/admin/products/${encodeURIComponent(longSku)}/edit`],
    ['create-product', '/admin/products/new'],
    ['admin-order', `/admin/orders/${order.order_id}`],
    ['stock-history', `/admin/products/${encodeURIComponent(longSku)}/stock`],
    ['import', '/admin/products/import'],
  ]
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    for (const [name, path] of screens) {
      await page.goto(path!)
      await page.waitForLoadState('networkidle')
      await page.screenshot({ path: resolve(output, `${name}-${width}.png`), fullPage: true })
      if (stage === 'after') {
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        if (name === 'edit-product') {
          await expect(page.getByRole('note', { name: 'Attention au renommage' })).toBeVisible()
          await page.getByRole('button', { name: 'Actualiser le produit' }).focus()
          await expect(page.getByRole('button', { name: 'Actualiser le produit' })).toBeFocused()
          const header = await page.locator('header.page-heading').boundingBox()
          const form = await page.locator('form').boundingBox()
          expect(header!.y + header!.height).toBeLessThan(form!.y)
        }
        if (name === 'stock-history') {
          await page.getByLabel('Variation de stock signée').fill('1')
          await page.getByLabel('Motif (obligatoire)').fill('Vérification visuelle uniquement')
          await page.getByRole('button', { name: 'Prévisualiser', exact: true }).click()
          await expect(page.getByRole('region', { name: 'Prévisualisation de l’ajustement' })).toBeVisible()
          await page.screenshot({ path: resolve(output, `stock-confirm-${width}.png`), fullPage: true })
          await expect(page.getByRole('button', { name: 'Confirmer l’ajustement', exact: true })).toBeDisabled()
          await page.getByRole('button', { name: 'Modifier', exact: true }).click()
        }
        if (name === 'admin-order') {
          expect(await page.locator('.order-actions').evaluate(e => getComputedStyle(e).gap)).toBe('12px')
          await page.getByRole('button', { name: 'Annuler la commande', exact: true }).click()
          await expect(page.getByRole('group', { name: 'Confirmation d’annulation' })).toBeVisible()
          await page.screenshot({ path: resolve(output, `order-confirm-${width}.png`), fullPage: true })
          await page.getByRole('button', { name: 'Conserver la commande' }).click()
          await expect(page.getByRole('group', { name: 'Confirmation d’annulation' })).toHaveCount(0)
        }
      }
    }
  }
  if (stage === 'after') {
    let release: (() => void) | undefined
    await page.route('**/api/backoffice/products/**', async route => {
      await new Promise<void>(resolve => { release = resolve })
      await route.fulfill({ json: product })
    })
    await page.goto(`/admin/products/${encodeURIComponent(longSku)}/edit`)
    await expect(page.getByText('Chargement du produit…', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Enregistrer', exact: true })).toBeDisabled()
    await page.screenshot({ path: resolve(output, 'edit-loading-390.png'), fullPage: true })
    release!()
    await page.unroute('**/api/backoffice/products/**')
    await page.route('**/api/backoffice/products/**', route => route.fulfill({ json: product }))
    await expect(page.getByRole('button', { name: 'Enregistrer', exact: true })).toBeEnabled()
    await page.getByRole('button', { name: 'Enregistrer', exact: true }).click()
    await expect(page.getByText('Métadonnées enregistrées.', { exact: true })).toBeVisible()
    await page.screenshot({ path: resolve(output, 'edit-success-390.png'), fullPage: true })
    await page.route('**/api/backoffice/products/**', route => route.fulfill({ status: 503, json: { detail: 'Erreur visuelle contrôlée' } }))
    await page.getByRole('button', { name: 'Actualiser le produit' }).click()
    await expect(page.locator('#form-error')).toBeVisible()
    await page.screenshot({ path: resolve(output, 'edit-error-390.png'), fullPage: true })
  }
})
