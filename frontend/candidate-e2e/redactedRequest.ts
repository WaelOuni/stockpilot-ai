/** Playwright transport errors include request headers, including bearer credentials. */
export async function redactedRequest<T>(operation: () => Promise<T>): Promise<T> {
  try {
    return await operation()
  } catch {
    // Do not retain a cause: reporters can print nested errors and their call logs.
    throw new Error('Candidate authenticated request failed; private diagnostics withheld.')
  }
}
