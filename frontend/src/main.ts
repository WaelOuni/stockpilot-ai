import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './style.css'
import { consumeDevLogin } from './utils/devAutoLogin'

if (import.meta.env.DEV) {
  consumeDevLogin(import.meta.env.VITE_DEV_AUTO_LOGIN === 'true', window.location.origin, document)
}

createApp(App).use(router).mount('#app')
