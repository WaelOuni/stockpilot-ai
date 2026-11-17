import { describe, expect, it } from 'vitest'
import sample from '../../ux-e2e/fixtures/intelligence.json'
import { analyticMoney, intelligenceDecoder, pageABC } from './intelligence'
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
  it('refuses ABC when a cost or comparable currency is missing', () => {
    const data = intelligenceDecoder(sample)
    expect(pageABC(data.items)).toBeNull()
    const items = data.items.slice(0, 3).map((item, index) => ({ ...item, stock_cost_minor: ['8000', '1500', '500'][index]! }))
    expect([...pageABC(items)!.values()]).toEqual(['A', 'B', 'C'])
    items[1]!.currency = 'USD'
    expect(pageABC(items)).toBeNull()
  })
})
