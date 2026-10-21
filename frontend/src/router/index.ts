import { createRouter, createWebHistory } from 'vue-router'
import AssistantPage from '../pages/AssistantPage.vue'
import OrdersPage from '../pages/OrdersPage.vue'
import OrderDetailPage from '../pages/OrderDetailPage.vue'
import ProductsPage from '../pages/ProductsPage.vue'
import ProductDetailPage from '../pages/ProductDetailPage.vue'
import AdminLoginPage from '../pages/AdminLoginPage.vue'
import AdminOrdersPage from '../pages/AdminOrdersPage.vue'
import AdminOrderDetailPage from '../pages/AdminOrderDetailPage.vue'
import AdminProductsPage from '../pages/AdminProductsPage.vue'
import AdminProductFormPage from '../pages/AdminProductFormPage.vue'
import AdminProductStockPage from '../pages/AdminProductStockPage.vue'
import AdminDashboardPage from '../pages/AdminDashboardPage.vue'
import AdminProductImportPage from '../pages/AdminProductImportPage.vue'
import { protectAdminRoutes } from './adminGuard'

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: "/b2b-app/", meta: { b2b: true }, component: () => import("../b2b/B2BApp.vue"), children: [
      { path: "", component: () => import("../b2b/CatalogueView.vue") },
      { path: "orders", component: () => import("../b2b/OrdersView.vue") },
      { path: "stock", component: () => import("../b2b/StockView.vue") },
      { path: ":pathMatch(.*)*", redirect: "/b2b-app/" },
    ] },
    { path: '/admin/login', component: AdminLoginPage },
    { path: '/admin/dashboard', component: AdminDashboardPage, meta: { admin: true } },
    { path: '/admin/orders', component: AdminOrdersPage, meta: { admin: true } },
    { path: '/admin/orders/:orderId', component: AdminOrderDetailPage, meta: { admin: true } },
    { path: '/admin/products', component: AdminProductsPage, meta: { admin: true } },
    { path: '/admin/products/new', component: AdminProductFormPage, meta: { admin: true } },
    { path: '/admin/products/import', component: AdminProductImportPage, meta: { admin: true } },
    { path: '/admin/products/:sku/edit', component: AdminProductFormPage, meta: { admin: true } },
    { path: '/admin/products/:sku/stock', component: AdminProductStockPage, meta: { admin: true } },
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
