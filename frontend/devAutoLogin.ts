import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import type { IncomingMessage } from 'node:http'
import type { Plugin } from 'vite'

const origins = new Set(['http://127.0.0.1:5173', 'http://localhost:5173'])

export function allowedDevRequest(request: Pick<IncomingMessage, 'headers' | 'socket'>): boolean {
  const origin = `http://${request.headers.host}`
  return origins.has(origin)
    && ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(request.socket.remoteAddress || '')
    && (!request.headers.origin || request.headers.origin === origin)
    && (!request.headers['sec-fetch-site'] || ['same-origin', 'none'].includes(String(request.headers['sec-fetch-site'])))
}

export function devAutoLogin(env: Record<string, string | undefined>, command: string, mode: string): Plugin[] {
  if (env.VITE_DEV_AUTO_LOGIN !== 'true') return []
  if (command !== 'serve' || mode !== 'development'
    || process.env.STOCKPILOT_DEMO_ENV_DIR || process.env.E2E_CUSTOMER_JWT_SECRET
    || !['http://127.0.0.1:8000', 'http://localhost:8000'].includes(env.API_PROXY_TARGET || 'http://127.0.0.1:8000')
    || (env.VITE_API_BASE_URL && env.VITE_API_BASE_URL !== '/api')) {
    throw new Error('DEV auto-login requires development serve, DEV backend on loopback:8000 and same-origin /api.')
  }
  let token = ''
  return [{
    name: 'stockpilot-local-customer-login',
    apply: 'serve',
    configResolved(config) {
      if (config.server.host !== '127.0.0.1' || config.server.port !== 5173 || !config.server.strictPort) {
        throw new Error('DEV auto-login requires 127.0.0.1:5173 with strictPort; no LAN binding.')
      }
    },
    configureServer(server) {
      try {
        token = execFileSync(resolve('../.venv/bin/python'),
          [resolve('../scripts/create_customer_token.py'), 'local-client-a', '--expires-in', '3600', '--dev-auto-login'],
          { cwd: resolve('..'), env: process.env, stdio: ['ignore', 'pipe', 'pipe'], timeout: 10000 }).toString().trim()
      } catch {
        // Never log subprocess output, which could contain credentials.
        throw new Error('DEV token generation refused. Export the DEV backend environment and its signing secret.')
      }
      if (!/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(token)) throw new Error('Invalid local token output')
      server.middlewares.use((request, response, next) => {
        if (!allowedDevRequest(request)) {
          response.statusCode = 403
          response.end('Local DEV origin required')
          return
        }
        // Vite's HTML middleware normally replaces this with no-cache.
        const setHeader = response.setHeader.bind(response)
        response.setHeader = (name, value) => setHeader(name,
          name.toLowerCase() === 'cache-control' ? 'no-store' : value)
        response.setHeader('Cache-Control', 'no-store')
        next()
      })
    },
    transformIndexHtml() {
      return [{ tag: 'meta', attrs: { name: 'stockpilot-dev-customer', content: token }, injectTo: 'head' }]
    },
  }]
}
