import { array, boolean, date, integer, minor, currency, nullable, object, oneOf, orderDecoder, text } from './contracts'
export const assistantCard = object({
  product_id: text, organization_id: text, sku: text, name: text,
  price_minor: minor, currency, quantity: integer, reserved: integer, available: integer,
  revision: integer, observed_at: date,
})
export const assistantTurn = object({
  turn_id: text, kind: oneOf('message', 'confirm', 'cancel'), message: text,
  status: oneOf('pending', 'completed'), created_at: date,
  operation: nullable(object({ namespace: oneOf('prepare', 'confirm', 'cancel'), operation_id: text })),
  reply: nullable(object({ intent: text, text, products: array(assistantCard, 5), order: nullable(orderDecoder), can_confirm: boolean })),
})
export const assistantHistory = object({
  conversation_id: text, items: array(assistantTurn, 20), has_more: boolean, limit: integer, offset: integer,
})
export const conversationList = object({
  items: array(object({ conversation_id: text, created_at: date }), 50), limit: integer, offset: integer,
})
export type AssistantTurn = ReturnType<typeof assistantTurn>
