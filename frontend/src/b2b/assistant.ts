import { array, boolean, date, integer, minor, currency, nullable, object, oneOf, orderDecoder, text, ContractError } from './contracts'
export const assistantCard = object({
  product_id: text, organization_id: text, sku: text, name: text,
  price_minor: minor, currency, quantity: integer, reserved: integer, available: integer,
  revision: integer, observed_at: date,
})
const turnDecoder = object({
  turn_id: text, kind: oneOf('message', 'confirm', 'cancel'), message: text,
  status: oneOf('pending', 'completed'), created_at: date,
  operation: nullable(object({ namespace: oneOf('prepare', 'confirm', 'cancel'), operation_id: text })),
  reply: nullable(object({ intent: text, text, products: array(assistantCard, 5), order: nullable(orderDecoder), can_confirm: boolean, can_browse_products: (value: unknown) => value === undefined ? false : boolean(value) })),
})
const executionDecoder = object({
  state: oneOf('running', 'failed', 'unknown', 'completed'),
  attempt_id: nullable((value: unknown) => { const id = text(value); if (!/^[0-9a-f]{32}$/.test(id)) throw new ContractError(); return id }),
  code: nullable(oneOf('provider_http_500', 'provider_http_503', 'provider_http_error', 'connect_timeout', 'read_timeout',
    'write_timeout', 'pool_timeout', 'transport_timeout', 'transport_error', 'response_decode_error', 'response_validation_error',
    'input_limit', 'client_cancelled', 'server_cancelled', 'global_budget_exceeded', 'dependency_unavailable', 'outcome_unknown',
    'authorization_denied', 'conflict', 'internal_error', 'busy')),
  retryable: boolean,
})
export function assistantTurn(value: unknown): ReturnType<typeof turnDecoder> & { execution?: ReturnType<typeof executionDecoder> } {
  const turn = turnDecoder(value)
  const state = (value as Record<string, unknown>).execution
  if (state === undefined) return turn // Historical responses stay readable.
  const execution = executionDecoder(state)
  if ((turn.status === 'completed') !== (execution.state === 'completed')) throw new ContractError()
  return { ...turn, execution }
}
export const assistantHistory = object({
  conversation_id: text, items: array(assistantTurn, 20), has_more: boolean, limit: integer, offset: integer,
})
export const conversationList = object({
  items: array(object({ conversation_id: text, created_at: date }), 50), limit: integer, offset: integer,
})
export type AssistantTurn = ReturnType<typeof assistantTurn>

export const assistantResults = object({
  items: array(assistantCard, 100), total: integer, has_more: boolean, limit: integer, offset: integer,
  observed_at: date,
  filters: object({
    minimum_available: nullable(integer), maximum_available: nullable(integer),
    exclude_sold_out: boolean, sort: oneOf('sku', 'available', 'price'),
  }),
})
