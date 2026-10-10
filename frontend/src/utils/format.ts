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

export function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}
