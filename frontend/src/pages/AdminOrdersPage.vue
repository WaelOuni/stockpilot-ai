<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { listAdminOrders, type AdminOrders } from '../api/admin'
import { ApiError } from '../api/http'
import AdminLogout from '../components/AdminLogout.vue'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import OrderStatusBadge from '../components/OrderStatusBadge.vue'
import { formatDate } from '../utils/format'
const route = useRoute()
const router = useRouter()
const page = ref<AdminOrders | null>(null)
const loading = ref(false)
const error = ref<Error | null>(null)
const offset = computed(() => {
  const raw = route.query.offset
  if (raw === undefined) return 0
  if (typeof raw !== 'string' || !/^(0|[1-9]\d*)$/.test(raw) || !Number.isSafeInteger(Number(raw))) return null
  return Number(raw)
})
let controller: AbortController | undefined
async function load() {
  controller?.abort()
  page.value = null
  error.value = null
  if (offset.value === null) { error.value = new Error('Pagination invalide.'); loading.value = false; return }
  const request = new AbortController()
  controller = request
  loading.value = true
  try { const result = await listAdminOrders(offset.value, request.signal); if (!request.signal.aborted) page.value = result }
  catch (cause) {
    if (!request.signal.aborted) {
      if (cause instanceof ApiError && cause.status === 401) await router.replace('/admin/login?reason=session')
      else error.value = cause instanceof Error ? cause : new Error('Chargement impossible.')
    }
  } finally { if (controller === request) loading.value = false }
}
watch(offset, () => { void load() }, { immediate: true })
onBeforeUnmount(() => controller?.abort())
function navigate(value: number) { void router.push({ path: '/admin/orders', query: value ? { offset: String(value) } : {} }) }
</script>
<template>
  <header class="page-heading">
    <span class="eyebrow">Back-office · Lecture seule</span><h1>Commandes administrateur</h1>
  </header>
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
    Chargement des commandes…
  </p>
  <ApiErrorNotice
    v-if="error"
    :error="error"
  />
  <template v-if="page">
    <p v-if="!page.items.length">
      Aucune commande sur cette page.
    </p>
    <article
      v-for="order in page.items"
      :key="order.order_id"
      class="context-card"
    >
      <h2>
        <RouterLink
          class="order-id"
          :to="`/admin/orders/${encodeURIComponent(order.order_id)}`"
        >
          {{ order.order_id }}
        </RouterLink>
      </h2>
      <p>Client : {{ order.customer_id ?? 'Non renseigné (legacy)' }}</p>
      <p>{{ order.product_name }} · Quantité : {{ order.quantity }}</p>
      <time :datetime="order.created_at">{{ formatDate(order.created_at) }}</time>
      <OrderStatusBadge :status="order.status" />
    </article>
    <nav
      class="pagination"
      aria-label="Pagination administrateur"
    >
      <button
        :disabled="loading || !offset"
        @click="navigate(Math.max(0, (offset ?? 0) - 20))"
      >
        Précédent
      </button>
      <span>Page {{ (offset ?? 0) / 20 + 1 }}</span>
      <button
        :disabled="loading || page.count < page.limit"
        @click="navigate((offset ?? 0) + 20)"
      >
        Suivant
      </button>
    </nav>
    <p class="hint">
      Une page complète peut être suivie d’une page vide ; aucun total n’est disponible.
    </p>
  </template>
</template>
