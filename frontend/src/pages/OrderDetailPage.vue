<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { getCustomerOrder } from '../api/client'
import { ApiError } from '../api/http'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import OrderStatusBadge from '../components/OrderStatusBadge.vue'
import OrderTimeline from '../components/OrderTimeline.vue'
import type { OrderResponse } from '../types/api'
import { customerToken } from '../utils/customerSession'
import { formatDate } from '../utils/format'

const route = useRoute()
const orderId = computed(() => String(route.params.orderId || ''))
const order = ref<OrderResponse | null>(null)
const loading = ref(false)
const error = ref<Error | null>(null)
const notFound = computed(() => error.value instanceof ApiError && error.value.status === 404)
let controller: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | null = null
let disposed = false
function stopPolling() {
  if (timer !== null) clearTimeout(timer)
  timer = null
}
async function load() {
  if (loading.value || !customerToken.value || !orderId.value || disposed) return
  stopPolling()
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = null
  try {
    const result = await getCustomerOrder(orderId.value, request.signal)
    if (request.signal.aborted) return
    order.value = result
    if (!['DELIVERED', 'CANCELLED'].includes(result.status)) {
      timer = setTimeout(() => { void load() }, 15_000)
    }
  } catch (cause) {
    if (!request.signal.aborted) {
      order.value = null
      error.value = cause instanceof Error ? cause : new Error('Erreur inattendue.')
    }
  } finally {
    if (controller === request) loading.value = false
  }
}
watch([orderId, customerToken], () => {
  stopPolling()
  controller?.abort()
  controller = null
  order.value = null
  error.value = null
  loading.value = false
  void load()
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => {
  disposed = true
  stopPolling()
  controller?.abort()
})
</script>

<template>
  <header class="page-heading">
    <RouterLink to="/orders">
      Retour à My Orders
    </RouterLink>
    <h1>Détail de commande</h1>
  </header>
  <section
    class="card order-detail"
    aria-label="Détail de la commande"
    :aria-busy="loading"
  >
    <div class="conversation-toolbar">
      <span class="eyebrow">Statut actuel</span>
      <button
        type="button"
        :disabled="loading || !customerToken"
        @click="load"
      >
        Actualiser
      </button>
    </div>
    <p
      v-if="!customerToken"
      class="empty-state"
    >
      Connectez un JWT client pour consulter cette commande.
    </p>
    <p
      v-if="loading"
      role="status"
      class="empty-state"
    >
      Actualisation de la commande...
    </p>
    <div
      v-if="notFound"
      class="empty-state"
      role="status"
    >
      <h2>Commande introuvable</h2>
      <p>Cette commande est introuvable pour votre session.</p>
    </div>
    <ApiErrorNotice
      v-else
      :error="error"
    />
    <div
      v-if="order"
      class="detail-content"
    >
      <h2>{{ order.product_name }}</h2>
      <OrderStatusBadge :status="order.status" />
      <dl>
        <div><dt>Quantité</dt><dd>{{ order.quantity }}</dd></div>
        <div><dt>Date de création</dt><dd>{{ formatDate(order.created_at) }}</dd></div>
        <div>
          <dt>Commande</dt><dd class="order-id">
            {{ order.order_id }}
          </dd>
        </div>
      </dl>
      <OrderTimeline :status="order.status" />
      <p class="hint">
        {{ ['DELIVERED', 'CANCELLED'].includes(order.status) ? 'Statut terminal : actualisation manuelle disponible.' : 'Actualisation automatique toutes les 15 secondes tant que cette page est ouverte.' }}
      </p>
    </div>
  </section>
</template>
