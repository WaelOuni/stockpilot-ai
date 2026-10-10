import type { OrderStatus } from '../types/api'

const messages: Record<OrderStatus, string> = {
  CREATED: 'Votre commande a été créée.',
  PROCESSING: 'Votre commande est en préparation.',
  SHIPPED: 'Votre commande a été expédiée.',
  DELIVERED: 'Votre commande a été livrée.',
  CANCELLED: 'Votre commande a été annulée.',
}

export function getOrderStatusMessage(status: OrderStatus): string {
  return messages[status]
}
