import { createRouter, createWebHistory } from 'vue-router'
import AssistantPage from '../pages/AssistantPage.vue'
import OrdersPage from '../pages/OrdersPage.vue'
import OrderDetailPage from '../pages/OrderDetailPage.vue'

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/assistant' },
    { path: '/assistant', component: AssistantPage },
    { path: '/orders', component: OrdersPage },
    { path: '/orders/:orderId', component: OrderDetailPage },
    { path: '/:pathMatch(.*)*', redirect: '/assistant' },
  ],
})
