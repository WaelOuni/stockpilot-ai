import { describe, expect, it } from 'vitest'
import { redactedRequest } from '../../candidate-e2e/redactedRequest'

describe('candidate authenticated browser requests', () => {
  it('preserves successful responses', async () => {
    const response = { status: 200 }
    await expect(redactedRequest(() => Promise.resolve(response))).resolves.toBe(response)
  })

  it('discards sensitive Playwright transport errors and their causes', async () => {
    const privateValue = 'synthetic-only-bearer-for-redaction-test'
    const transportError = new Error(`ECONNREFUSED\nCall log:\nAuthorization: Bearer ${privateValue}`, {
      cause: new Error(`nested private value: ${privateValue}`),
    })
    const failure: unknown = await redactedRequest(() => Promise.reject(transportError)).catch(error => error)
    expect(failure).toBeInstanceOf(Error)
    const safe = failure as Error
    expect(safe.message).toBe('Candidate authenticated request failed; private diagnostics withheld.')
    expect(safe).not.toBe(transportError)
    expect(safe.cause).toBeUndefined()
    expect(String(safe)).not.toContain(privateValue)
    expect(safe.stack).not.toContain(privateValue)
    expect(safe.stack).not.toContain('Authorization:')
  })

  it('also redacts errors thrown before a promise is returned', async () => {
    await expect(redactedRequest(() => { throw new Error('synthetic private request options') }))
      .rejects.toThrow('Candidate authenticated request failed; private diagnostics withheld.')
  })
})
