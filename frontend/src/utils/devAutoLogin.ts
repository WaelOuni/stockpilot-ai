import { setCustomerToken } from './customerSession'

export async function consumeDevLogin(
  enabled: boolean, location: Pick<Location, 'origin' | 'hash' | 'pathname'>,
  transport: typeof fetch = fetch,
): Promise<boolean> {
  const key = new URLSearchParams(location.hash.slice(1)).get('stockpilot-dev-login') || ''
  if (!enabled || !['http://127.0.0.1:5173', 'http://localhost:5173'].includes(location.origin)
    || location.pathname.toLowerCase().startsWith('/admin')
    || !/^[0-9a-f]{64}$/.test(key)) return false
  try {
    const response = await transport('/__stockpilot_dev_login', {
      method: 'POST', headers: { 'X-StockPilot-Dev-Login': key },
      credentials: 'omit', cache: 'no-store', redirect: 'error',
    })
    if (!response.ok) return false
    const result: unknown = await response.json()
    if (!result || typeof result !== 'object' || !('token' in result)
      || typeof result.token !== 'string'
      || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(result.token)) return false
    setCustomerToken(result.token)
    return true
  } catch {
    return false
  }
}
