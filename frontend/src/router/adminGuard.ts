import type { Router } from 'vue-router'
import { adminMe } from '../api/admin'
import { ApiError } from '../api/http'

export function protectAdminRoutes(router: Router): void {
  router.beforeEach(async (to) => {
    if (!to.meta.admin) return true
    try { await adminMe(); return true }
    catch (cause) {
      return { path: '/admin/login', query: {
        reason: cause instanceof ApiError && cause.status === 401 ? 'session' : 'unavailable',
      } }
    }
  })
}
