import { array, boolean, ContractError, currency, date, integer, minor, nullable, object, oneOf, signedInteger, text, type Decoder } from './contracts'
import { language } from './preferences'

const finite: Decoder<number> = value => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > Number.MAX_SAFE_INTEGER) throw new ContractError()
  return value
}
// Indicative ratios are not exact integer quantities. A valid large ratio must
// not invalidate the stock/monetary facts; its display is guarded separately.
const coverage: Decoder<number> = value => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new ContractError()
  return value
}
const units: Decoder<string> = value => {
  const result = text(value)
  if (!/^(0|[1-9][0-9]{0,49})$/.test(result)) throw new ContractError()
  return result
}
const signedMoney: Decoder<string> = value => {
  const result = text(value)
  if (!/^(0|-?[1-9][0-9]{0,49})$/.test(result)) throw new ContractError()
  return result
}
const boundedUnits = (maximum: bigint): Decoder<string> => value => {
  const result = units(value)
  if (BigInt(result) > maximum) throw new ContractError()
  return result
}
const unitMargin: Decoder<string> = value => {
  const result = signedMoney(value), amount = BigInt(result)
  if (amount < -9223372036854775807n || amount > 9223372036854775807n) throw new ContractError()
  return result
}
const unverifiedAvailability: Decoder<false> = value => {
  if (value !== false) throw new ContractError()
  return false
}
const day: Decoder<string> = value => {
  const result = text(value)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(result) || !Number.isFinite(Date.parse(result + 'T00:00:00Z')) || new Date(result + 'T00:00:00Z').toISOString().slice(0, 10) !== result) throw new ContractError()
  return result
}
const forecast = object({ status: oneOf('estimated', 'insufficient_history', 'no_commercial_history', 'mixed_dataset', 'incomplete_history', 'stockout_censored'),
  horizon_days: integer, observations: integer, method: nullable(oneOf('naive', 'mean28', 'weighted28', 'ses02', 'croston_sba', 'seasonal_naive7')),
  quantity: nullable(finite), daily: array(finite, 90),
  backtest: nullable(object({ basis: oneOf('retrospective_final_snapshot'), availability_verified: unverifiedAvailability, horizon_days: integer, selection_windows: integer, assessment_windows: integer, mae_units: finite, rmse_units: finite, naive_mae_units: finite, assessment_start: day, assessment_end: day })),
  uncertainty: nullable(object({ kind: oneOf('historical_error_range'), lower: finite, upper: finite, assessment_windows: integer, probability: (v: unknown) => { if (v !== null) throw new ContractError(); return null } })) })
export const intelligenceItem = object({ product_id: text, sku: text, name: text, category: text, status: oneOf('active', 'inactive'), price_minor: minor, currency,
  quantity: minor, reserved: minor, available: minor, period_sales_units: nullable(integer), period_returns_units: nullable(integer), mean_daily_sales: nullable(finite),
  sales_to_current_stock_ratio: nullable(finite), dormant: boolean, period_complete: boolean, history_days: integer,
  source_references: array(text, 20), source_reference_count: integer, dataset: oneOf('observed', 'DEMO_SYNTHETIC', 'mixed', 'unavailable'), forecast,
  replenishment: object({ status: oneOf('missing_supply_parameters', 'forecast_unavailable', 'recommendation', 'inactive_product'), suggested_quantity: nullable(integer), coverage_days: nullable(coverage), reorder_point: nullable(integer), lead_time_days: nullable(integer), safety_stock: nullable(integer), minimum_order_quantity: nullable(integer), incoming_quantity: nullable(integer) }),
  unit_cost_minor: nullable(minor), supplier: nullable(text), stock_cost_minor: nullable(boundedUnits(9223372036854775807n ** 2n)),
  supply_parameters: nullable(object({ lead_time_days: nullable(integer), safety_stock: nullable(integer), minimum_order_quantity: nullable(integer), incoming_confirmed: nullable(integer), incoming_as_of: nullable(day), source_references: array(text, 20) })),
  promotion_scenarios: array(object({ discount_bps: integer, price_minor: minor, unit_margin_minor: nullable(unitMargin), baseline_unit_margin_minor: nullable(unitMargin),
    demand_uplift: (value: unknown) => { if (value !== null) throw new ContractError(); return null }, profitability: oneOf('not_estimated') }), 4), reasons: array(text, 20) })
const wire = object({ organization_id: text, refreshed_at: date, period_start: day, period_end_exclusive: day, horizon_days: integer, low_stock_threshold: integer,
  summary: object({ products: integer, quantity: units, reserved: units, available: units, out_of_stock: integer, low_stock: integer,
    retail_value_by_currency: array(object({ currency, amount_minor: nullable(boundedUnits(10n ** 30n)) }), 200), valuation_basis: oneOf('current_list_price_not_purchase_cost'),
    source: oneOf('b2b_products'), scope: oneOf('all_filtered_products'), status: oneOf('active', 'inactive') }),
  items: array(intelligenceItem, 20), total: integer, limit: integer, offset: integer, has_more: boolean,
  page_summary: object({ products: integer, dormant_products: integer, forecastable_products: integer, mean_coverage_days: nullable(coverage) }),
  page_series: array(object({ date: day, datasets: array(object({ dataset: oneOf('observed', 'DEMO_SYNTHETIC'), complete_products: integer, expected_products: integer,
    sold: nullable(integer), returned: nullable(integer), received: nullable(integer), adjusted: nullable(signedInteger), reserved: nullable(integer), cancelled: nullable(integer), transferred: nullable(integer) }), 2) }), 365),
  limitations: array(text, 20), explanation_mode: oneOf('deterministic'), automatic_actions: boolean })
export type IntelligenceData = ReturnType<typeof wire>
export type IntelligenceItem = ReturnType<typeof intelligenceItem>
export const intelligenceDecoder: Decoder<IntelligenceData> = value => {
  const result = wire(value)
  if (result.automatic_actions || result.limit > 20 || result.items.length > result.limit || result.page_summary.products !== result.items.length || result.summary.products !== result.total) throw new ContractError()
  for (const item of result.items) {
    if (BigInt(item.reserved) > BigInt(item.quantity) || BigInt(item.available) !== (item.status === 'active' ? BigInt(item.quantity) - BigInt(item.reserved) : 0n)) throw new ContractError()
    const f = item.forecast
    if (f.horizon_days !== result.horizon_days || (f.status === 'estimated' ? f.quantity === null || f.method === null || f.backtest === null || f.daily.length !== f.horizon_days : f.quantity !== null || f.daily.length !== 0)) throw new ContractError()
    if (item.promotion_scenarios.some(s => s.discount_bps > 9000)) throw new ContractError()
  }
  for (const row of result.page_series) {
    if (new Set(row.datasets.map(bucket => bucket.dataset)).size !== row.datasets.length) throw new ContractError()
    for (const bucket of row.datasets) {
      if (bucket.expected_products < 1 || bucket.expected_products > result.items.length || bucket.complete_products > bucket.expected_products) throw new ContractError()
      const complete = bucket.complete_products === bucket.expected_products
      for (const field of ['sold', 'returned', 'received', 'adjusted', 'reserved', 'cancelled', 'transferred'] as const) {
        const value = bucket[field]
        if ((value !== null) !== complete || (value !== null && Math.abs(value) > bucket.expected_products * 1e9)) throw new ContractError()
      }
    }
  }
  return result
}

/** Convert exact integer strings only inside the faithfully representable range. */
export function chartNumber(value: number | string | null | undefined): number | null {
  if (value == null) return null
  if (typeof value === 'string') {
    const amount = BigInt(signedMoney(value))
    return amount >= BigInt(Number.MIN_SAFE_INTEGER) && amount <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(amount) : null
  }
  return Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER ? value : null
}

export function analyticNumber(value: number | string | null | undefined): string {
  if (value == null) return language.value === 'fr' ? 'Non disponible' : 'Unavailable'
  if (typeof value === 'number' && chartNumber(value) === null) return language.value === 'fr' ? 'Hors plage de visualisation' : 'Outside visualization range'
  return new Intl.NumberFormat(language.value === 'fr' ? 'fr-FR' : 'en-GB', { maximumFractionDigits: 2 }).format(typeof value === 'string' ? BigInt(signedMoney(value)) : value)
}

export function analyticMoney(value: string, code: string): string {
  signedMoney(value); currency(code)
  const amount = BigInt(value), absolute = amount < 0 ? -amount : amount
  if (!Intl.supportedValuesOf('currency').includes(code)) return `${value} ${code} (${language.value === 'fr' ? 'unités mineures' : 'minor units'})`
  const formatter = new Intl.NumberFormat(language.value === 'fr' ? 'fr-FR' : 'en-GB', { style: 'currency', currency: code })
  const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2, scale = 10n ** BigInt(digits)
  return (amount < 0 ? '−' : '') + formatter.formatToParts(absolute / scale).map(part => part.type === 'fraction' ? (absolute % scale).toString().padStart(digits, '0') : part.value).join('')
}

// An explicitly page-scoped classification of known current stock cost, not
// annual consumption ABC and never a cross-currency comparison.
export function pageABC(items: IntelligenceItem[]): Map<string, string> | null {
  if (!items.length || items.some(item => item.stock_cost_minor === null) || new Set(items.map(item => item.currency)).size !== 1) return null
  const sorted = [...items].sort((a, b) => BigInt(a.stock_cost_minor!) > BigInt(b.stock_cost_minor!) ? -1 : BigInt(a.stock_cost_minor!) < BigInt(b.stock_cost_minor!) ? 1 : a.sku.localeCompare(b.sku))
  const total = sorted.reduce((sum, item) => sum + BigInt(item.stock_cost_minor!), 0n)
  if (total === 0n) return null
  let cumulative = 0n
  return new Map(sorted.map(item => {
    const group = cumulative * 100n < total * 80n ? 'A' : cumulative * 100n < total * 95n ? 'B' : 'C'
    cumulative += BigInt(item.stock_cost_minor!)
    return [item.product_id, group]
  }))
}
