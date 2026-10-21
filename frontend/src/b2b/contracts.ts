// Wire decoders: unknown JSON never becomes trusted business state by assertion.
export type Decoder<T> = (value: unknown) => T
export class ContractError extends Error {
  constructor() { super('Réponse du service incompatible. Aucune action automatique effectuée.') }
}
export const text: Decoder<string> = v => {
  if (typeof v !== 'string' || v.length > 16384) throw new ContractError()
  return v
}
export const integer: Decoder<number> = v => {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < 0) throw new ContractError()
  return v
}
export const signedInteger: Decoder<number> = v => {
  if (typeof v !== 'number' || !Number.isSafeInteger(v)) throw new ContractError()
  return v
}
export const boolean: Decoder<boolean> = v => {
  if (typeof v !== 'boolean') throw new ContractError()
  return v
}
export const minor: Decoder<string> = v => {
  const result = text(v)
  if (!/^(0|[1-9][0-9]{0,18})$/.test(result) || BigInt(result) > 9223372036854775807n) throw new ContractError()
  return result
}
export const date: Decoder<string> = v => {
  const result = text(v)
  if (!/^\d{4}-\d{2}-\d{2}T/.test(result) || !Number.isFinite(Date.parse(result))) throw new ContractError()
  return result
}
export const currency: Decoder<string> = v => {
  const result = text(v)
  if (!/^[A-Z]{3}$/.test(result)) throw new ContractError()
  return result
}
export function oneOf<const T extends readonly string[]>(...values: T): Decoder<T[number]> {
  return v => { if (typeof v !== 'string' || !values.includes(v)) throw new ContractError(); return v as T[number] }
}
export function nullable<T>(decode: Decoder<T>): Decoder<T | null> {
  return v => v === null ? null : decode(v)
}
export function array<T>(decode: Decoder<T>, maximum = 100): Decoder<T[]> {
  return v => { if (!Array.isArray(v) || v.length > maximum) throw new ContractError(); return v.map(decode) }
}
export function object<S extends Record<string, Decoder<unknown>>>(shape: S): Decoder<{ [K in keyof S]: ReturnType<S[K]> }> {
  return v => {
    if (typeof v !== 'object' || v === null || Array.isArray(v)) throw new ContractError()
    const source = v as Record<string, unknown>
    return Object.fromEntries(Object.entries(shape).map(([key, decode]) => [key, decode(source[key])])) as { [K in keyof S]: ReturnType<S[K]> }
  }
}
export function page<T>(decode: Decoder<T>) {
  return object({ items: array(decode), limit: integer, offset: integer })
}
export const sessionDecoder = object({
  user_id: text, organization_id: text, organization_name: text,
  role: oneOf('READ_ONLY', 'USER', 'MANAGER', 'ORGANIZATION_ADMIN'),
  permissions: array(text, 50), csrf_token: text, expires_at: date, idle_expires_at: date,
})
export type Session = ReturnType<typeof sessionDecoder>
export const productDecoder = object({
  product_id: text, organization_id: text, sku: text, name: text, description: text, category: text,
  price_minor: minor, currency, status: oneOf('active', 'inactive'), attributes: array(text), revision: integer,
})
export type Product = ReturnType<typeof productDecoder>
export const productsDecoder = object({
  items: array(productDecoder), total: integer, has_more: boolean, limit: integer, offset: integer,
})
const stockFields = object({
  product_id: text, organization_id: text, quantity: integer, reserved: integer,
  available: integer, status: oneOf('active', 'inactive'), revision: integer,
})
export const stockDecoder: Decoder<ReturnType<typeof stockFields>> = v => {
  const stock = stockFields(v)
  if (stock.reserved > stock.quantity || stock.available !== (stock.status === 'active' ? stock.quantity - stock.reserved : 0)) throw new ContractError()
  return stock
}
export type Stock = ReturnType<typeof stockDecoder>
export const buyerDecoder = object({
  association_id: text, organization_id: text, user_id: text, customer_id: text, status: oneOf('active'), revision: text,
})
export const orderDecoder = object({
  organization_id: text, order_id: text, customer_id: text, created_by: text,
  status: oneOf('prepared', 'reserved', 'cancelled'),
  lines: array(object({ product_id: text, sku: text, quantity: integer, price_minor: minor, currency }), 20),
  operation_id: text, preparation_digest: text, created_at: date, expires_at: date,
  reserved_at: nullable(date), cancelled_at: nullable(date), revision: integer,
})
export type Order = ReturnType<typeof orderDecoder>
export const resultDecoder = object({
  operation_id: text, namespace: oneOf('adjust', 'import'), organization_id: text, actor_user_id: text,
  status: oneOf('pending', 'succeeded', 'rejected'), code: text, movement_ids: array(text),
  import_id: nullable(text),
})
export type OperationResult = ReturnType<typeof resultDecoder>
export const previewDecoder = object({
  import_id: text, organization_id: text, actor_user_id: text,
  mode: oneOf('create_only', 'metadata_only', 'stock_only'), status: oneOf('preview', 'invalid', 'applied', 'rejected'),
  file_fingerprint: text, preview_digest: text, rows: integer,
  issues: array(object({ line: integer, code: text, message: text }), 50), expires_at: date,
})
export type Preview = ReturnType<typeof previewDecoder>
export const movementDecoder = object({
  movement_id: text, organization_id: text, product_id: text, sku: text, operation_id: text, actor_user_id: text,
  kind: text, operation_namespace: text, quantity_before: integer, quantity_after: integer,
  reserved_before: integer, reserved_after: integer, revision_after: integer, delta: signedInteger,
  reason: text, source_reference: text, occurred_at: date,
})
export type Movement = ReturnType<typeof movementDecoder>
