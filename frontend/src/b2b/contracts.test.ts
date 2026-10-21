import { describe, expect, it } from 'vitest'
import { ContractError, orderDecoder, previewDecoder, productDecoder, sessionDecoder, stockDecoder } from './contracts'
import { formatMoney } from './money'
import { orderFixture, previewFixture, productFixture, sessionFixture, stockFixture } from './fixtures'
import { physicalQuantity, positiveQuantity } from './workflows'

describe('exact B2B money and wire contracts', () => {
  it.each([
    ['4900', 'EUR', '49,00'],
    ['9007199254740993', 'EUR', '90 071 992 547 409,93'],
    ['1234', 'JPY', '1 234'],
    ['1234', 'KWD', '1,234'],
    ['0', 'EUR', '0,00'],
    ['9223372036854775807', 'EUR', '92 233 720 368 547 758,07'],
  ])('formats %s %s without floating arithmetic', (minor, currency, expected) => {
    expect(formatMoney(minor, currency)).toContain(expected)
  })
  it('does not invent a minor-unit exponent for an unknown currency', () => {
    expect(formatMoney('1234', 'ZZZ')).toBe('1234 unités mineures (ZZZ, précision inconnue)')
  })
  it('rejects inconsistent physical/reserved/available response fields', () => {
    expect(() => stockDecoder({ ...stockFixture, reserved: 11 })).toThrow(ContractError)
    expect(() => stockDecoder({ ...stockFixture, available: 9 })).toThrow(ContractError)
  })
  it.each(['-1', '1.5', '1e2', '004', 'NaN'])('rejects non contractual price %s', value => {
    expect(() => formatMoney(value, 'EUR')).toThrow(ContractError)
  })
  it('rejects numeric wire money instead of rounding it', () => {
    expect(() => productDecoder({ ...productFixture, price_minor: Number('9007199254740993') })).toThrow(ContractError)
  })
  it('does not infer missing session identity or privileged role', () => {
    expect(() => sessionDecoder({ ...sessionFixture, organization_id: undefined })).toThrow(ContractError)
    expect(() => sessionDecoder({ ...sessionFixture, role: 'PLATFORM_ADMIN' })).toThrow(ContractError)
  })
  it('exposes only the session contract, never provider token extras', () => {
    expect(sessionDecoder({ ...sessionFixture, access_token: 'unexpected' })).not.toHaveProperty('access_token')
  })
  it('does not accept a mismatched order state or preview status', () => {
    expect(() => orderDecoder({ ...orderFixture, status: 'confirmed' })).toThrow(ContractError)
    expect(() => previewDecoder({ ...previewFixture, status: 'ready' })).toThrow(ContractError)
    expect(previewDecoder(previewFixture).status).toBe('preview')
  })
  it.each(['0', '-1', '1.5', '18V', 'true', '1000001'])('rejects invalid order quantity %s', value => {
    expect(() => positiveQuantity(value)).toThrow()
  })
  it('allows integer physical zero but never negative or fractional physical stock', () => {
    expect(physicalQuantity('0')).toBe(0)
    expect(positiveQuantity('1')).toBe(1)
    expect(() => physicalQuantity('-1')).toThrow()
    expect(() => physicalQuantity('0.5')).toThrow()
  })
})
