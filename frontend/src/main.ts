import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './style.css'
import { consumeDevLogin } from './utils/devAutoLogin'

async function bootstrap(): Promise<void> {
  if (import.meta.env.DEV) {
    const launchHash = window.location.hash
    if (import.meta.env.VITE_DEV_AUTO_LOGIN === 'true'
      && ['http://127.0.0.1:5173', 'http://localhost:5173'].includes(window.location.origin)
      && /^#stockpilot-dev-login=[0-9a-f]{64}$/.test(launchHash)) {
      // Keep the ephemeral launch fragment across client navigation for reloads.
      router.beforeEach(to => !to.hash && !to.path.toLowerCase().startsWith('/admin')
        ? { ...to, hash: launchHash } : undefined)
    }
    const connected = await consumeDevLogin(import.meta.env.VITE_DEV_AUTO_LOGIN === 'true', window.location)
    if (import.meta.env.VITE_DEV_AUTO_LOGIN === 'true' && !connected && !window.location.pathname.toLowerCase().startsWith('/admin')) {
      console.warn('DEV auto-login unavailable. Open the private local link printed by Vite, or use the JWT panel.')
    }
  }
  createApp(App).use(router).mount('#app')
}

void bootstrap()
