import type { Decoder } from './contracts'
import { BffError } from './client'
import type { B2BSession, OperationRef } from './session'

export async function mutate<T>(session: B2BSession, family: OperationRef['family'], namespace: OperationRef['namespace'],
  path: string, body: (id: string) => unknown, decode: Decoder<T>): Promise<T> {
  // A pending/unknown write must be reconciled, never replaced with another key.
  if (session.pending.value.length) throw new Error('Vérifiez d’abord les opérations en attente.')
  const operation = session.remember(family, namespace)
  try {
    const result = await session.client.request(path, decode, { method: 'POST', body: body(operation.id) })
    if (!(typeof result === 'object' && result !== null && 'status' in result && result.status === 'pending')) session.resolve(operation)
    return result
  } catch (error) {
    // Pre-dispatch rejections and the typed transactional stock refusal are definitive.
    // Generic conflicts, timeouts and missing receipts remain quarantined.
    if (error instanceof BffError && ([400, 413, 422, 429].includes(error.status) || (error.status === 409 && error.code === 'STOCK_INSUFFICIENT' && !!error.stock))) session.resolve(operation)
    throw error
  }
}
export function positiveQuantity(value: string): number {
  if (!/^[1-9][0-9]{0,6}$/.test(value) || Number(value) > 1000000) throw new Error('Saisissez une quantité entière de 1 à 1 000 000.')
  return Number(value)
}
export function physicalQuantity(value: string): number {
  if (!/^(0|[1-9][0-9]{0,9})$/.test(value) || Number(value) > 1000000000) throw new Error('Saisissez un stock physique entier de 0 à 1 000 000 000.')
  return Number(value)
}
