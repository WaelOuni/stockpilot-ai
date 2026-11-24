import { describe, expect, it } from 'vitest'
import sample from '../../ux-e2e/fixtures/intelligence.json'
import { analyticMoney, analyticNumber, chartNumber, intelligenceDecoder, pageABC } from './intelligence'
import { ContractError } from './contracts'
import { language } from './preferences'

describe('Intelligence API contract from a real isolated backend fixture', () => {
  it('accepts the scoped read-only fixture without recomputing its forecasts', () => {
    const result = intelligenceDecoder(sample)
    expect(result.items).toHaveLength(20)
    expect(result.items[0]!.forecast.quantity).toBe(14)
    expect(result.automatic_actions).toBe(false)
    expect(result.items[0]!.dataset).toBe('DEMO_SYNTHETIC')
  })
  it.each(['foreign_number', 'action', 'stock', 'invented_confidence', 'missing_daily'])('rejects inconsistent or unbounded data: %s', kind => {
    const invalid = structuredClone(sample)
    if (kind === 'foreign_number') invalid.page_summary.mean_coverage_days = Number.POSITIVE_INFINITY
    if (kind === 'action') invalid.automatic_actions = true
    if (kind === 'stock') invalid.items[0]!.available = '999'
    if (kind === 'invented_confidence') Object.assign(invalid.items[0]!.forecast.uncertainty!, { probability: 0.95 })
    if (kind === 'missing_daily') invalid.items[0]!.forecast.daily.pop()
    expect(() => intelligenceDecoder(invalid)).toThrow(ContractError)
  })
  it('keeps large and negative monetary amounts exact', () => {
    language.value = 'en'
    expect(analyticMoney('900719925474099312345', 'EUR')).toBe('€9,007,199,254,740,993,123.45')
    expect(analyticMoney('-101', 'EUR')).toBe('−€1.01')
  })
  it('accepts a valid int64 stock with coverage above the chart precision limit', () => {
    const huge = structuredClone(sample)
    huge.items[0]!.quantity = '9223372036854775807'
    huge.items[0]!.reserved = '0'
    huge.items[0]!.available = '9223372036854775807'
    huge.items[0]!.replenishment.coverage_days = 4.611686018427388e18
    huge.page_summary.mean_coverage_days = 4.611686018427388e18
    expect(intelligenceDecoder(huge).items[0]!.available).toBe('9223372036854775807')
  })
  it.each([Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER + 1, 4.611686018427388e18, Number.MAX_VALUE, null])('keeps a valid indicative coverage metric without dropping the response: %s', value => {
    const valid = intelligenceDecoder(sample)
    valid.page_summary.mean_coverage_days = value
    expect(intelligenceDecoder(valid).page_summary.mean_coverage_days).toBe(value)
  })
  it.each([Number.NaN, Infinity, -1, '9007199254740993', true])('still rejects an invalid coverage metric: %s', value => {
    const invalid = structuredClone(sample)
    Object.assign(invalid.page_summary, { mean_coverage_days: value })
    expect(() => intelligenceDecoder(invalid)).toThrow(ContractError)
  })
  it('preserves exact integer strings and money at their business limits', () => {
    const data = intelligenceDecoder(sample), item = data.items[0]!
    item.quantity = item.available = item.unit_cost_minor = item.price_minor = '9223372036854775807'
    item.reserved = '0'
    item.stock_cost_minor = (9223372036854775807n ** 2n).toString()
    item.promotion_scenarios[0]!.unit_margin_minor = '-9223372036854775807'
    data.summary.retail_value_by_currency[0]!.amount_minor = (10n ** 30n).toString()
    expect(intelligenceDecoder(data).items[0]!.stock_cost_minor).toBe(item.stock_cost_minor)
    language.value = 'en'
    expect(analyticNumber('9007199254740993')).toBe('9,007,199,254,740,993')
    expect(analyticMoney(item.stock_cost_minor, 'EUR')).toBe('€850,705,917,302,346,158,473,969,077,842,325,012.49')
  })
  it.each(['quantity', 'price_minor', 'unit_cost_minor', 'stock_cost_minor', 'margin', 'valuation'])('rejects business overflow: %s', field => {
    const data = intelligenceDecoder(sample), item = data.items[0]!
    if (field === 'quantity' || field === 'price_minor' || field === 'unit_cost_minor') item[field] = '9223372036854775808'
    if (field === 'stock_cost_minor') item.stock_cost_minor = (9223372036854775807n ** 2n + 1n).toString()
    if (field === 'margin') item.promotion_scenarios[0]!.unit_margin_minor = '-9223372036854775808'
    if (field === 'valuation') data.summary.retail_value_by_currency[0]!.amount_minor = (10n ** 30n + 1n).toString()
    expect(() => intelligenceDecoder(data)).toThrow(ContractError)
  })
  it.each(['01', '-0', '1e5', '1.0', ' 1', '9'.repeat(51)])('rejects invalid exact integer encodings: %s', value => {
    expect(() => analyticMoney(value, 'EUR')).toThrow(ContractError)
  })
  it.each(['fr', 'en'] as const)('announces unavailable and visualization limits in %s', locale => {
    language.value = locale
    expect(chartNumber('9007199254740991')).toBe(Number.MAX_SAFE_INTEGER)
    expect(chartNumber('-9007199254740991')).toBe(Number.MIN_SAFE_INTEGER)
    expect(chartNumber('9007199254740992')).toBeNull()
    expect(chartNumber('-9007199254740992')).toBeNull()
    expect(chartNumber(4.611686018427388e18)).toBeNull()
    expect(chartNumber(null)).toBeNull()
    expect(analyticNumber(null)).toBe(locale === 'fr' ? 'Non disponible' : 'Unavailable')
    expect(analyticNumber(4.611686018427388e18)).toBe(locale === 'fr' ? 'Hors plage de visualisation' : 'Outside visualization range')
  })
  it('rejects false provenance completeness and a false availability proof', () => {
    const data = intelligenceDecoder(sample)
    data.page_series[0]!.datasets[0]!.complete_products--
    expect(() => intelligenceDecoder(data)).toThrow(ContractError)
    const invalid = structuredClone(sample)
    Object.assign(invalid.items[0]!.forecast.backtest!, { availability_verified: true })
    expect(() => intelligenceDecoder(invalid)).toThrow(ContractError)
  })
  it('refuses ABC when a cost or comparable currency is missing', () => {
    const data = intelligenceDecoder(sample)
    expect(pageABC(data.items)).toBeNull()
    const items = data.items.slice(0, 3).map((item, index) => ({ ...item, stock_cost_minor: ['8000', '1500', '500'][index]! }))
    expect([...pageABC(items)!.values()]).toEqual(['A', 'B', 'C'])
    items[1]!.currency = 'USD'
    expect(pageABC(items)).toBeNull()
  })
})
