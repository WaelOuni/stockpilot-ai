export interface PendingStockAdjustment {
  sku: string
  operationId: string
  delta: number
  reason: string
}

const STORAGE_KEY = 'stockpilot.pending-stock-adjustment.v1'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

export function readPendingStockAdjustment(): PendingStockAdjustment | null {
  const encoded = sessionStorage.getItem(STORAGE_KEY)
  if (encoded === null) return null
  let value: unknown
  try {
    value = JSON.parse(encoded)
  } catch {
    throw new Error('Opération de stock locale invalide. Rapprochement manuel requis.')
  }
  if (!value || typeof value !== 'object') {
    throw new Error('Opération de stock locale invalide. Rapprochement manuel requis.')
  }
  const pending = value as Partial<PendingStockAdjustment>
  if (typeof pending.sku !== 'string' || !pending.sku.trim()
    || typeof pending.operationId !== 'string' || !UUID.test(pending.operationId)
    || !Number.isSafeInteger(pending.delta) || pending.delta === 0
    || Math.abs(pending.delta!) > 1_000_000
    || typeof pending.reason !== 'string' || !pending.reason.trim()
    || pending.reason.trim().length > 500) {
    throw new Error('Opération de stock locale invalide. Rapprochement manuel requis.')
  }
  return {
    sku: pending.sku,
    operationId: pending.operationId,
    delta: pending.delta!,
    reason: pending.reason.trim(),
  }
}

export function savePendingStockAdjustment(pending: PendingStockAdjustment): void {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(pending))
}

export function clearPendingStockAdjustment(): void {
  sessionStorage.removeItem(STORAGE_KEY)
}
