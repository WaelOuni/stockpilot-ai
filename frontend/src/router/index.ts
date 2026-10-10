import { createRouter, createWebHistory } from 'vue-router'
import AssistantPage from '../pages/AssistantPage.vue'
import OrdersPage from '../pages/OrdersPage.vue'
import OrderDetailPage from '../pages/OrderDetailPage.vue'
import ProductsPage from '../pages/ProductsPage.vue'
import ProductDetailPage from '../pages/ProductDetailPage.vue'
import AdminLoginPage from '../pages/AdminLoginPage.vue'
import AdminOrdersPage from '../pages/AdminOrdersPage.vue'
import AdminOrderDetailPage from '../pages/AdminOrderDetailPage.vue'
import { protectAdminRoutes } from './adminGuard'

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/admin/login', component: AdminLoginPage },
    { path: '/admin/orders', component: AdminOrdersPage, meta: { admin: true } },
    { path: '/admin/orders/:orderId', component: AdminOrderDetailPage, meta: { admin: true } },
    { path: '/', redirect: '/assistant' },
    { path: '/assistant', component: AssistantPage, props: (route) => ({
      initialMessage: typeof route.query.message === 'string' ? route.query.message : '',
    }) },
    { path: '/products', component: ProductsPage },
    { path: '/products/:sku', component: ProductDetailPage },
    { path: '/orders', component: OrdersPage },
    { path: '/orders/:orderId', component: OrderDetailPage },
    { path: '/:pathMatch(.*)*', redirect: '/assistant' },
  ],
})
protectAdminRoutes(router)
