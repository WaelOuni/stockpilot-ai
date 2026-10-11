import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { IncomingMessage } from 'node:http'
import { allowedDevRequest, devAutoLogin } from '../../devAutoLogin'
import { consumeDevLogin } from './devAutoLogin'
import { customerToken, setCustomerToken } from './customerSession'

beforeEach(() => {
  setCustomerToken('')
  document.head.innerHTML = '<meta name="stockpilot-dev-customer" content="test-only-token">'
})
afterEach(() => vi.unstubAllEnvs())

it('restores memory on reload and removes the transient meta without storage', () => {
  consumeDevLogin(true, 'http://127.0.0.1:5173', document)
  expect(customerToken.value).toBe('test-only-token')
  expect(document.querySelector('meta')).toBeNull()
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
  setCustomerToken('')
  expect(customerToken.value).toBe('')
})

it.each(['https://localhost:5173', 'http://evil.example:5173', 'http://localhost:5174'])('refuses origin %s', origin => {
  consumeDevLogin(true, origin, document)
  expect(customerToken.value).toBe('')
})
it('is disabled by default', () => {
  consumeDevLogin(false, 'http://localhost:5173', document)
  expect(customerToken.value).toBe('')
  expect(devAutoLogin({}, 'build', 'production')).toEqual([])
})
it.each([['build', 'production'], ['build', 'development'], ['serve', 'production'], ['serve', 'e2e']])('rejects %s %s', (command, mode) => {
  expect(() => devAutoLogin({ VITE_DEV_AUTO_LOGIN: 'true' }, command, mode)).toThrow()
})
it.each(['STOCKPILOT_DEMO_ENV_DIR', 'E2E_CUSTOMER_JWT_SECRET'])('rejects %s isolation marker', name => {
  vi.stubEnv(name, 'test-only-marker')
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
