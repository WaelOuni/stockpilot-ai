import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { randomBytes, timingSafeEqual } from 'node:crypto'
import type { IncomingMessage } from 'node:http'
import type { Plugin } from 'vite'

const loginPath = '/__stockpilot_dev_login'
const origins = new Set(['http://127.0.0.1:5173', 'http://localhost:5173'])

export function allowedDevRequest(request: Pick<IncomingMessage, 'headers' | 'socket'>): boolean {
  const origin = `http://${request.headers.host}`
  return !Object.keys(request.headers).some(name => /^(forwarded|via|x-real-ip|x-forwarded-.*)$/.test(name))
    && origins.has(origin)
    && ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(request.socket.remoteAddress || '')
    && (!request.headers.origin || request.headers.origin === origin)
    && (!request.headers['sec-fetch-site'] || ['same-origin', 'none'].includes(String(request.headers['sec-fetch-site'])))
}

export function allowedDevLogin(request: Pick<IncomingMessage, 'headers' | 'socket' | 'method'>, capability: string): boolean {
  const key = request.headers['x-stockpilot-dev-login']
  return allowedDevRequest(request) && request.method === 'POST'
    && request.headers.origin === `http://${request.headers.host}`
    && request.headers['sec-fetch-site'] === 'same-origin'
    && request.headers['sec-fetch-mode'] === 'cors'
    && request.headers['sec-fetch-dest'] === 'empty'
    && /^[0-9a-f]{64}$/.test(capability)
    && typeof key === 'string' && /^[0-9a-f]{64}$/.test(key)
    && timingSafeEqual(Buffer.from(key), Buffer.from(capability))
}

export function devAutoLogin(env: Record<string, string | undefined>, command: string, mode: string): Plugin[] {
  if (env.VITE_DEV_AUTO_LOGIN !== 'true') return []
  if (process.env.NODE_ENV === 'production' || command !== 'serve' || mode !== 'development'
    || process.env.STOCKPILOT_DEMO_ENV_DIR || process.env.E2E_CUSTOMER_JWT_SECRET
    || !['http://127.0.0.1:8000', 'http://localhost:8000'].includes(env.API_PROXY_TARGET || 'http://127.0.0.1:8000')
    || (env.VITE_API_BASE_URL && env.VITE_API_BASE_URL !== '/api')) {
    throw new Error('DEV auto-login requires development serve, DEV backend on loopback:8000 and same-origin /api.')
  }
  let token = ''
  const capability = randomBytes(32).toString('hex')
  let expiresAt = 0
  return [{
    name: 'stockpilot-local-customer-login',
    apply: 'serve',
    configResolved(config) {
      const proxy = config.server.proxy?.['/api']
      const target = typeof proxy === 'string' ? proxy : proxy?.target?.toString()
      if (config.server.https || config.server.host !== '127.0.0.1' || config.server.port !== 5173 || !config.server.strictPort) {
        throw new Error('DEV auto-login requires 127.0.0.1:5173 with strictPort; no LAN binding.')
      }
      if (!target || !['http://127.0.0.1:8000', 'http://localhost:8000'].includes(target)) {
        throw new Error('Effective /api proxy must target the local DEV backend on port 8000.')
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
      expiresAt = Date.now() + 3600_000
      server.httpServer?.once('listening', () => {
        // Local terminal only: this is an ephemeral access key, never the JWT.
        server.config.logger.info(`DEV client (private local link, do not share): http://127.0.0.1:5173/#stockpilot-dev-login=${capability}`)
      })
      server.middlewares.use((request, response, next) => {
        if (!allowedDevRequest(request)) {
          response.statusCode = 403
          response.end('Local DEV origin required')
          return
        }
        if (request.url?.split('?')[0] === loginPath) {
          response.setHeader('Cache-Control', 'no-store')
          response.setHeader('Content-Type', 'application/json')
          if (!allowedDevLogin(request, capability) || Date.now() >= expiresAt) {
            response.statusCode = 403
            response.end('{}')
          } else {
            response.end(JSON.stringify({ token }))
          }
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
  }]
}
