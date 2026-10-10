<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { getAdminOrder, type AdminOrder } from '../api/admin'
import { ApiError } from '../api/http'
import AdminLogout from '../components/AdminLogout.vue'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import OrderStatusBadge from '../components/OrderStatusBadge.vue'
import OrderTimeline from '../components/OrderTimeline.vue'
import { formatDate } from '../utils/format'
const route = useRoute()
const router = useRouter()
const orderId = computed(() => String(route.params.orderId || ''))
const order = ref<AdminOrder | null>(null)
const loading = ref(false)
const error = ref<Error | null>(null)
let controller: AbortController | undefined
async function load() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  loading.value = true
  order.value = null
  error.value = null
  try { const result = await getAdminOrder(orderId.value, request.signal); if (!request.signal.aborted) order.value = result }
  catch (cause) {
    if (!request.signal.aborted) {
      if (cause instanceof ApiError && cause.status === 401) await router.replace('/admin/login?reason=session')
      else error.value = cause instanceof Error ? cause : new Error('Chargement impossible.')
    }
  } finally { if (controller === request) loading.value = false }
}
watch(orderId, () => { void load() }, { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <header class="page-heading">
    <span class="eyebrow">Back-office · Lecture seule</span><h1>Détail administratif</h1>
  </header>
  <RouterLink to="/admin/orders">
    Retour aux commandes administrateur
  </RouterLink>
  <AdminLogout />
  <button
    :disabled="loading"
    @click="load"
  >
    Actualiser
  </button>
  <p
    v-if="loading"
    role="status"
  >
    Chargement de la commande…
  </p>
  <ApiErrorNotice
    v-if="error"
    :error="error"
  />
  <article
    v-if="order"
    class="context-card"
  >
    <h2>{{ order.order_id }}</h2>
    <p>Client : {{ order.customer_id ?? 'Non renseigné (legacy)' }}</p>
    <p>{{ order.product_name }} · Quantité : {{ order.quantity }}</p>
    <time :datetime="order.created_at">{{ formatDate(order.created_at) }}</time>
    <OrderStatusBadge :status="order.status" />
    <OrderTimeline
      :status="order.status"
      :events="order.events"
    />
  </article>
</template>
