import { setCustomerToken } from './customerSession'

export function consumeDevLogin(enabled: boolean, origin: string, document: Document): void {
  const element = document.querySelector<HTMLMetaElement>('meta[name="stockpilot-dev-customer"]')
  const token = element?.content || ''
  element?.remove()
  if (enabled && ['http://127.0.0.1:5173', 'http://localhost:5173'].includes(origin) && token) {
    setCustomerToken(token)
  }
}
