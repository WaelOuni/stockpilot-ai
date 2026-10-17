import type { OrderStatus } from '../types/api'

const labels: Record<OrderStatus, string> = {
  CREATED: 'Creee',
  PROCESSING: 'En preparation',
  SHIPPED: 'Expediee',
  DELIVERED: 'Livree',
  CANCELLED: 'Annulee',
}
export function orderStatusLabel(status: OrderStatus): string {
  return labels[status] || status
}

export function isOrderStatus(value: unknown): value is OrderStatus {
  return typeof value === 'string' && Object.hasOwn(labels, value)
}

export function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

// Catalogue prices have no currency field. Keep their numeric precision; never add a currency symbol.
export function formatCatalogueNumber(value: number): string {
  const [coefficient, exponent = '0'] = value.toString().split('e')
  const decimals = (coefficient!.split('.')[1]?.length || 0) - Number(exponent)
  if (decimals > 20) {
    return new Intl.NumberFormat('fr-FR', { notation: 'scientific', maximumSignificantDigits: 21 }).format(value)
  }
  return new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 20 }).format(value === 0 ? 0 : value)
}

// Differences are computed with binary floats by the API; remove subtraction noise for display only.
export function formatPriceDifference(value: number): string {
  return formatCatalogueNumber(Number(value.toPrecision(15)))
}
