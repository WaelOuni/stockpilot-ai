import { describe, expect, it } from 'vitest'
import { formatCatalogueNumber, formatPriceDifference } from './format'

describe('Catalogue numbers without a currency assumption', () => {
  it.each([[49.9, '49,90'], [0, '0,00'], [-0, '0,00'], [1234.567, '1\u202f234,567'], [0.001, '0,001'], [-20, '-20,00'], [1e-21, '1E-21']])(
    'formats %s with French separators and preserves numeric precision', (value, text) => {
      const result = formatCatalogueNumber(Number(value))
      expect(result).toBe(text)
      expect(result).not.toMatch(/€|EUR|USD|\$/)
    },
  )
  it('formats a derived difference without binary subtraction noise', () => {
    expect(formatPriceDifference(60 - 49.9)).toBe('10,10')
    expect(formatPriceDifference(69.99 - 49.9)).toBe('20,09')
  })
})
