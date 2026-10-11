import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { IncomingMessage } from 'node:http'
import type { ResolvedConfig } from 'vite'
import { allowedDevRequest, allowedDevLogin, devAutoLogin } from '../../devAutoLogin'
import { consumeDevLogin } from './devAutoLogin'
import { customerToken, setCustomerToken } from './customerSession'

const key = 'a'.repeat(64)
const token = 'test.payload.signature'
const location = { origin: 'http://127.0.0.1:5173', hash: `#stockpilot-dev-login=${key}`, pathname: '/' }
beforeEach(() => setCustomerToken(''))
afterEach(() => vi.unstubAllEnvs())

it('authenticates with launch key, keeping JWT only in memory', async () => {
  const transport = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ token }) })
  expect(await consumeDevLogin(true, location, transport)).toBe(true)
  expect(customerToken.value).toBe(token)
  expect(transport).toHaveBeenCalledWith('/__stockpilot_dev_login', expect.objectContaining({
    method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error',
    headers: { 'X-StockPilot-Dev-Login': key },
  }))
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
  expect(document.cookie).toBe('')
})
it.each([
  { origin: 'https://localhost:5173' }, { origin: 'https://tunnel.example' },
  { origin: 'http://localhost:5174' }, { hash: '' }, { hash: '#stockpilot-dev-login=invalid' },
  { pathname: '/admin' }, { pathname: '/admin/products' }, { pathname: '/ADMIN/login' }, { pathname: '/Admin/orders' },
])('never fetches credentials for forbidden browser configuration %j', async extra => {
  const transport = vi.fn()
  expect(await consumeDevLogin(true, { ...location, ...extra }, transport)).toBe(false)
  expect(transport).not.toHaveBeenCalled()
  expect(customerToken.value).toBe('')
})
it('is disabled by default', async () => {
  const transport = vi.fn()
  expect(await consumeDevLogin(false, location, transport)).toBe(false)
  expect(transport).not.toHaveBeenCalled()
  expect(devAutoLogin({}, 'build', 'production')).toEqual([])
})
it.each(['network', '403', 'invalid'])('fails closed on %s', async failure => {
  const transport = vi.fn().mockImplementation(async () => {
    if (failure === 'network') throw new Error('offline')
    return { ok: failure !== '403', json: async () => ({ token: 'invalid' }) }
  })
  expect(await consumeDevLogin(true, location, transport)).toBe(false)
  expect(customerToken.value).toBe('')
})
it.each([['build', 'production'], ['build', 'development'], ['serve', 'production'], ['serve', 'e2e']])('rejects %s %s', (command, mode) => {
  expect(() => devAutoLogin({ VITE_DEV_AUTO_LOGIN: 'true' }, command, mode)).toThrow()
})
it.each(['STOCKPILOT_DEMO_ENV_DIR', 'E2E_CUSTOMER_JWT_SECRET'])('rejects %s isolation marker', name => {
  vi.stubEnv(name, 'test-only-marker')
  expect(() => devAutoLogin({ VITE_DEV_AUTO_LOGIN: 'true' }, 'serve', 'development')).toThrow()
})
it.each([
  { host: '0.0.0.0' }, { host: true }, { host: 'localhost' },
  { port: 5174 }, { strictPort: false }, { https: {} },
  { proxy: undefined }, { proxy: { '/api': { target: 'https://remote.example' } } },
])('rejects unsafe effective Vite configuration %j', extra => {
  const hook = devAutoLogin({ VITE_DEV_AUTO_LOGIN: 'true' }, 'serve', 'development')[0]!.configResolved
  expect(typeof hook).toBe('function')
  const resolved = { server: { host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000' } }, ...extra } } as ResolvedConfig
  expect(() => (hook as (config: ResolvedConfig) => void)(resolved)).toThrow()
})
it('refuses NODE_ENV=production even when mode is development', () => {
  vi.stubEnv('NODE_ENV', 'production')
  expect(() => devAutoLogin({ VITE_DEV_AUTO_LOGIN: 'true' }, 'serve', 'development')).toThrow()
})
it.each([{ API_PROXY_TARGET: 'https://remote.example' }, { VITE_API_BASE_URL: 'https://remote.example' }])('rejects remote backend configuration', extra => {
  expect(() => devAutoLogin({ VITE_DEV_AUTO_LOGIN: 'true', ...extra }, 'serve', 'development')).toThrow()
})
it.each([
  ['localhost:5173', '127.0.0.1', undefined, undefined, true],
  ['evil.example:5173', '127.0.0.1', undefined, undefined, false],
  ['localhost:5173', '192.168.1.1', undefined, undefined, false],
  ['localhost:5173', '127.0.0.1', 'http://evil.example', undefined, false],
  ['localhost:5173', '127.0.0.1', undefined, 'cross-site', false],
])('checks Host, peer, Origin and Fetch Metadata', (host, remoteAddress, origin, site, expected) => {
  const request = { headers: { host, origin, 'sec-fetch-site': site }, socket: { remoteAddress } } as Pick<IncomingMessage, 'headers' | 'socket'>
  expect(allowedDevRequest(request)).toBe(expected)
})

it.each(['forwarded', 'x-forwarded-host', 'x-forwarded-for', 'x-real-ip', 'via'])('refuses proxy marker %s', header => {
  const request = { headers: { host: 'localhost:5173', [header]: 'proxy' }, socket: { remoteAddress: '127.0.0.1' } } as Pick<IncomingMessage, 'headers' | 'socket'>
  expect(allowedDevRequest(request)).toBe(false)
})
it.each(['valid', 'missing_key', 'wrong_key', 'missing_origin', 'remote_origin', 'missing_metadata', 'cross_site', 'GET'])('protects local credential retrieval: %s', variant => {
  const headers: Record<string, string> = { host: 'localhost:5173', origin: 'http://localhost:5173',
    'sec-fetch-site': 'same-origin', 'sec-fetch-mode': 'cors', 'sec-fetch-dest': 'empty',
    'x-stockpilot-dev-login': key }
  if (variant === 'missing_key') delete headers['x-stockpilot-dev-login']
  if (variant === 'wrong_key') headers['x-stockpilot-dev-login'] = 'b'.repeat(64)
  if (variant === 'missing_origin') delete headers.origin
  if (variant === 'remote_origin') headers.origin = 'https://tunnel.example'
  if (variant === 'missing_metadata') delete headers['sec-fetch-site']
  if (variant === 'cross_site') headers['sec-fetch-site'] = 'cross-site'
  const request = { headers, socket: { remoteAddress: '127.0.0.1' }, method: variant === 'GET' ? 'GET' : 'POST' } as Pick<IncomingMessage, 'headers' | 'socket' | 'method'>
  expect(allowedDevLogin(request, key)).toBe(variant === 'valid')
})
