import { expect, test } from '@playwright/test'
import { createServer, request as upstream } from 'node:http'
import type { AddressInfo } from 'node:net'

test('BFF POST sends exact Origin and only an origin Referer, never private path/query', async ({ page }) => {
  // A real local HTTP peer observes the browser's final headers. Route mocks
  // alone did not catch Origin:null under the former no-referrer policy.
  const observed: { origin?: string; referer?: string; csrf?: string }[] = []
  const server = createServer((req, res) => {
    if (req.method === 'POST' && req.url === '/b2b/api/assistant/conversations/probe/messages') {
      observed.push({ origin: req.headers.origin, referer: req.headers.referer, csrf: String(req.headers['x-bff-csrf'] ?? '') })
      req.resume()
      req.on('end', () => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify('ok')) })
    } else if (req.url?.startsWith('/src/') || req.url?.startsWith('/node_modules/') || req.url?.startsWith('/@')) {
      // Vite supplies the real TS module; API calls stay on this test peer.
      const proxy = upstream('http://127.0.0.1:5186' + req.url, response => {
        res.writeHead(response.statusCode ?? 502, response.headers); response.pipe(res)
      })
      proxy.on('error', () => { res.statusCode = 502; res.end() }); proxy.end()
    } else { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><title>Synthetic header probe</title>') }
  })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const origin = 'http://127.0.0.1:' + (server.address() as AddressInfo).port
  try {
    await page.goto(origin + '/private/conversation/SYNTHETIC_PATH?sentinel=SYNTHETIC_QUERY')
    const result = await page.evaluate(async () => {
      const source = '/src/b2b/client.ts'
      const { BffClient } = await import(source)
      const client = new BffClient({ generation: () => 1, csrf: () => 'synthetic-csrf', invalid: () => {} })
      return client.request('/b2b/api/assistant/conversations/probe/messages', (v: unknown) => v, { method: 'POST', body: {} })
    })
    expect(result).toBe('ok')
    expect(observed).toEqual([{ origin, referer: origin + '/', csrf: 'synthetic-csrf' }])
    expect(JSON.stringify(observed)).not.toContain('SYNTHETIC_PATH')
    expect(JSON.stringify(observed)).not.toContain('SYNTHETIC_QUERY')
  } finally {
    server.closeAllConnections()
    await new Promise<void>(resolve => server.close(() => resolve()))
  }
})
