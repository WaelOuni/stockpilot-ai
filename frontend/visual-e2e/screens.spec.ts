import { test, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { customerHeaders, syntheticCustomerToken } from '../e2e/support/customer_auth'

test('all existing screens at desktop, tablet and mobile', async ({ page }) => {
  test.setTimeout(240_000)
  const stage = process.env.VISUAL_STAGE || 'after'
  const output = resolve('../artifacts/ui-ux', stage)
  mkdirSync(output, { recursive: true })
  const errors: string[] = []
  page.on('console', message => { if (message.type() === 'error') errors.push('Browser console error') })
  page.on('requestfailed', request => { if (request.failure()?.errorText !== 'net::ERR_ABORTED') errors.push(`Network failure: ${new URL(request.url()).pathname}`) })
  page.on('pageerror', () => errors.push('Uncaught browser error'))
  page.on('response', response => { if (response.status() >= 500) errors.push(`HTTP ${response.status()}: ${new URL(response.url()).pathname}`) })
  await page.goto('/admin/login')
  await page.getByLabel('Identifiant administrateur').fill(process.env.E2E_ADMIN_USERNAME!)
  await page.getByLabel('Mot de passe administrateur').fill(process.env.E2E_ADMIN_PASSWORD!)
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page).toHaveURL(/admin\/orders/)
  const orders = await page.evaluate(async () => (await fetch('/api/backoffice/orders?limit=1', { credentials: 'include' })).json())
  const order = orders.items[0]
  const customer = order?.customer_id || 'visual-review-customer'
  const jwt = syntheticCustomerToken(customer)
  const products = await page.evaluate(async headers =>
    (await fetch('/api/products?limit=1', { headers })).json(), customerHeaders(jwt))
  const product = products.items[0]
  const screens = [
    ['catalogue', '/products'], ['product', `/products/${encodeURIComponent(product.sku)}`],
    ['assistant', '/assistant'], ['customer-orders', '/orders'],
    ['dashboard', '/admin/dashboard'], ['admin-orders', '/admin/orders'],
    ['admin-products', '/admin/products'], ['create-product', '/admin/products/new'],
    ['edit-product', `/admin/products/${encodeURIComponent(product.sku)}/edit`],
    ['stock-history', `/admin/products/${encodeURIComponent(product.sku)}/stock`],
    ['import', '/admin/products/import'],
    ...(order ? [['admin-order', `/admin/orders/${encodeURIComponent(order.order_id)}`], ['customer-order', `/orders/${encodeURIComponent(order.order_id)}`]] : []),
  ]
  const results: object[] = []
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    for (const [name, path] of screens) {
      await page.goto(path!)
      if (!path!.startsWith('/admin')) {
        await page.getByLabel('JWT client', { exact: true }).fill(jwt)
        await page.getByRole('button', { name: 'Connecter', exact: true }).click()
      }
      await page.waitForLoadState('networkidle')
      await expect(page.locator('h1')).toBeVisible()
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)
      results.push({ name, width, overflow })
      await page.screenshot({ path: resolve(output, `${name}-${width}.png`), fullPage: true })
    }
    // Use a separate anonymous context for the login page; never capture filled credentials.
    const anonymous = await page.context().browser()!.newContext({ viewport: { width, height: 1000 } })
    const login = await anonymous.newPage()
    await login.goto('http://127.0.0.1:5174/admin/login')
    await login.screenshot({ path: resolve(output, `login-${width}.png`), fullPage: true })
    await anonymous.close()
  }
  writeFileSync(resolve(output, 'checks.json'), JSON.stringify({ results, errors }, null, 2))
  expect(errors).toEqual([])
  if (stage === 'after') expect(results.filter(row => (row as { overflow: boolean }).overflow)).toEqual([])
})
