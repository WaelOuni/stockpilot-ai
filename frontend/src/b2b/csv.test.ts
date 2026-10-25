import { describe, expect, it } from 'vitest'
import { encodeCsv, priceInput, priceMinor } from './csv'

describe('safe spreadsheet downloads and exact prices', () => {
  it('quotes separators, quotes, newlines and Unicode without changing import values', () => {
    expect(encodeCsv([['Éclairage, bureau', 'avec "LED"\nréglable', '=literal']])).toBe('"Éclairage, bureau","avec ""LED""\nréglable","=literal"\r\n')
  })
  it('neutralizes formulas only for downloads, including whitespace-prefixed formulas', () => {
    expect(encodeCsv([['=1+1', ' \t@SUM(A1)', '+1', '-1', 'normal']], true)).toBe('"\'=1+1","\' \t@SUM(A1)","\'+1","\'-1","normal"\r\n')
  })
  it('preserves int64 prices without floating point rounding', () => {
    expect(priceMinor('92233720368547758,07', 'EUR')).toBe('9223372036854775807')
    expect(priceInput('9223372036854775807', 'EUR')).toBe('92233720368547758.07')
    expect(priceMinor('1.234', 'TND')).toBe('1234')
    expect(priceMinor('123', 'JPY')).toBe('123')
  })
  it.each(['-1', '1e3', '1.001', 'NaN', '92233720368547758.08'])('rejects invalid EUR price %s', value => {
    expect(() => priceMinor(value, 'EUR')).toThrow()
  })
})
