import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import AdminDashboardPage from './AdminDashboardPage.vue'
import AdminLoginPage from './AdminLoginPage.vue'
import { protectAdminRoutes } from '../router/adminGuard'
import type { BackofficeDashboard } from '../types/api'

const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined
const zeroCounts = { CREATED: 0, PROCESSING: 0, SHIPPED: 0, DELIVERED: 0, CANCELLED: 0 }
function response(overrides: Partial<BackofficeDashboard> = {}): BackofficeDashboard {
  return {
    order_stats: {
      total_orders: 2,
      by_status: { ...zeroCounts, CREATED: 1, DELIVERED: 1 },
    },
    product_stats: {
      total_products: 3, available_products: 1, unavailable_products: 2, total_stock_units: 4,
    },
    recent_orders: [{
      order_id: 'order-latest', product_name: 'Bosch X100', quantity: 2,
      status: 'CREATED', created_at: '2026-10-10T10:00:00Z',
    }],
    recent_stock_movements: [{
      product_sku: 'SKU/1', delta: -1, occurred_at: '2026-10-10T11:00:00Z',
      reason: 'Ajustement de test',
    }],
    ...overrides,
  }
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockImplementation(async input => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' })
    : json(response()))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.unstubAllGlobals()
})

async function open(path = '/admin/dashboard') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin/login', component: AdminLoginPage },
      { path: '/admin/dashboard', component: AdminDashboardPage, meta: { admin: true } },
      { path: '/admin/orders', component: { template: '<p>Orders page</p>' }, meta: { admin: true } },
      { path: '/admin/orders/:id', component: { template: '<p>Order detail</p>' }, meta: { admin: true } },
      { path: '/admin/products', component: { template: '<p>Products page</p>' }, meta: { admin: true } },
      { path: '/admin/products/:sku/stock', component: { template: '<p>Stock history</p>' }, meta: { admin: true } },
    ],
  })
  protectAdminRoutes(router)
  await router.push(path)
  wrapper = mount(RouterView, { global: { plugins: [router] } })
  await flushPromises()
  return router
}

function button(name: string) {
  return wrapper!.findAll('button').find(item => item.text() === name)!
}

it('renders measured order/product metrics and links to real admin details', async () => {
  await open()

  expect(wrapper!.get('h1').text()).toBe('Tableau de bord')
  expect(wrapper!.text()).toContain('Total des commandes')
  expect(wrapper!.text()).toContain('Bosch X100')
  expect(wrapper!.text()).toContain('Ajustements de stock récents')
  expect(wrapper!.text()).toContain('Affichage des cinq derniers ajustements')
  expect(wrapper!.find('a[href="/admin/orders/order-latest"]').exists()).toBe(true)
  expect(wrapper!.find('a[href="/admin/products/SKU%2F1/stock"]').exists()).toBe(true)
  expect(wrapper!.text()).toContain('aucun seuil de stock faible')
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/dashboard'))).toHaveLength(1)
})

it('renders explicit empty states without fabricated recent activity', async () => {
  fetchMock.mockImplementation(async input => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' })
    : json(response({
      order_stats: { total_orders: 0, by_status: { ...zeroCounts } },
      product_stats: {
        total_products: 0, available_products: 0, unavailable_products: 0, total_stock_units: 0,
      },
      recent_orders: [],
      recent_stock_movements: [],
    })))
  await open()

  expect(wrapper!.text()).toContain('Aucune commande enregistrée.')
  expect(wrapper!.text()).toContain('Aucun ajustement de stock enregistré.')
  expect(wrapper!.text()).toContain('Unités de stock au total')
  expect(wrapper!.findAll('.dashboard-list')).toHaveLength(0)
})

it('ignores a stale dashboard response after leaving and reopening the route', async () => {
  let resolveOld!: (value: Response) => void
  let dashboardCall = 0
  fetchMock.mockImplementation(async input => {
    if (String(input).includes('/session/')) return json({ username: 'admin', csrf_token: 'csrf' })
    dashboardCall++
    if (dashboardCall === 1) return new Promise<Response>(resolve => { resolveOld = resolve })
    return json(response({
      order_stats: { total_orders: 8, by_status: { ...zeroCounts, PROCESSING: 8 } },
      recent_orders: [{
        order_id: 'new-result', product_name: 'New product', quantity: 1,
        status: 'PROCESSING', created_at: '2026-10-10T12:00:00Z',
      }],
      recent_stock_movements: [],
    }))
  })
  const router = await open()
  expect(wrapper!.get('[role="status"]').text()).toContain('Chargement')

  await router.push('/admin/orders')
  await flushPromises()
  await router.push('/admin/dashboard')
  await flushPromises()
  expect(wrapper!.text()).toContain('new-result')
  resolveOld(json(response({
    order_stats: { total_orders: 1, by_status: { ...zeroCounts, CREATED: 1 } },
    recent_orders: [{
      order_id: 'stale-result', product_name: 'Stale product', quantity: 1,
      status: 'CREATED', created_at: '2026-10-10T09:00:00Z',
    }],
    recent_stock_movements: [],
  })))
  await flushPromises()

  expect(wrapper!.text()).toContain('new-result')
  expect(wrapper!.text()).not.toContain('stale-result')
  expect(dashboardCall).toBe(2)
})

it('shows a safe retryable error and then loads a successful refresh', async () => {
  fetchMock.mockImplementationOnce(async () => json({ username: 'admin', csrf_token: 'csrf' }))
  fetchMock.mockImplementationOnce(async () => json({ detail: 'private database detail' }, 500))
  const router = await open()
  expect(wrapper!.get('[role="alert"]').text()).not.toContain('private database detail')
  expect(wrapper!.text()).not.toContain('private database detail')
  fetchMock.mockResolvedValue(json(response()))

  await button('Actualiser').trigger('click')
  await flushPromises()

  expect(router.currentRoute.value.path).toBe('/admin/dashboard')
  expect(wrapper!.text()).toContain('order-latest')
  expect(wrapper!.find('[role="alert"]').exists()).toBe(false)
})

it('redirects immediately when a dashboard read reports an expired session', async () => {
  fetchMock.mockImplementation(async input => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' })
    : json({ detail: 'expired' }, 401))
  const router = await open()

  expect(router.currentRoute.value.fullPath).toBe('/admin/login?reason=session')
  expect(wrapper!.get('h1').text()).toBe('Connexion administrateur')
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/dashboard'))).toHaveLength(1)
})

it('refuses extra/internal dashboard fields and malformed recent values in the API contract', async () => {
  const base = response() as BackofficeDashboard & Record<string, unknown>
  fetchMock.mockImplementation(async input => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' })
    : json({ ...base, recent_stock_movements: [{ ...base.recent_stock_movements[0], operation_id: 'private' }] }))
  await open()
  expect(wrapper!.get('[role="alert"]').text()).toContain('Réponse du tableau de bord invalide.')
  expect(wrapper!.text()).not.toContain('private')
})
