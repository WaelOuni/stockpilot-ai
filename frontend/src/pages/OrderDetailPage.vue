<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { cancelOrder, getCustomerOrder } from '../api/client'
import { ApiError } from '../api/http'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import OrderStatusBadge from '../components/OrderStatusBadge.vue'
import OrderTimeline from '../components/OrderTimeline.vue'
import OrderStatusNotification from '../components/OrderStatusNotification.vue'
import type { OrderResponse, OrderStatus } from '../types/api'
import { customerToken } from '../utils/customerSession'
import { formatDate } from '../utils/format'

const route = useRoute()
const orderId = computed(() => String(route.params.orderId || ''))
const order = ref<OrderResponse | null>(null)
const loading = ref(false)
const error = ref<Error | null>(null)
const lastLoadedStatus = ref<OrderStatus | null>(null)
const statusNotification = ref<{ status: OrderStatus; sequence: number } | null>(null)
let notificationSequence = 0
const confirming = ref(false)
const cancelling = ref(false)
const cancellationNotice = ref('')
const cancellationError = ref<Error | null>(null)
const needsReconciliation = ref(false)
const canCancel = computed(() => order.value !== null &&
  ['CREATED', 'PROCESSING'].includes(order.value.status) && !needsReconciliation.value)
const notFound = computed(() => error.value instanceof ApiError && error.value.status === 404)
let controller: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | null = null
let disposed = false
function stopPolling() {
  if (timer !== null) clearTimeout(timer)
  timer = null
}
async function load() {
  if (loading.value || cancelling.value || !customerToken.value || !orderId.value || disposed) return
  stopPolling()
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = null
  try {
    const result = await getCustomerOrder(orderId.value, request.signal)
    if (request.signal.aborted) return
    if (lastLoadedStatus.value !== null && lastLoadedStatus.value !== result.status) {
      statusNotification.value = { status: result.status, sequence: ++notificationSequence }
    }
    lastLoadedStatus.value = result.status
    order.value = result
    if (needsReconciliation.value) {
      cancellationNotice.value = result.status === 'CANCELLED'
        ? 'Statut vérifié : commande annulée.'
        : `Statut vérifié : ${result.status}.`
    }
    needsReconciliation.value = false
    if (!['CREATED', 'PROCESSING'].includes(result.status)) confirming.value = false
    if (!confirming.value && !['DELIVERED', 'CANCELLED'].includes(result.status)) {
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
function beginCancellation() {
  if (!canCancel.value || loading.value || cancelling.value) return
  stopPolling()
  cancellationNotice.value = ''
  cancellationError.value = null
  confirming.value = true
}
function returnToDetail() {
  if (cancelling.value) return
  confirming.value = false
  void load()
}
async function confirmCancellation() {
  if (!confirming.value || !canCancel.value || loading.value || cancelling.value || disposed) return
  stopPolling()
  const request = new AbortController()
  controller = request
  cancelling.value = true
  cancellationError.value = null
  cancellationNotice.value = ''
  try {
    const result = await cancelOrder(orderId.value, request.signal)
    if (request.signal.aborted) return
    order.value = result
    lastLoadedStatus.value = result.status
    statusNotification.value = null
    confirming.value = false
    cancellationNotice.value = 'Commande annulée.'
  } catch (cause) {
    if (request.signal.aborted) return
    cancellationError.value = cause instanceof ApiError && cause.status === 409
      ? new ApiError('Cette commande ne peut plus être annulée. Son statut a changé.', cause.status, cause.requestId)
      : cause instanceof Error ? cause : new Error('Erreur inattendue.')
    confirming.value = false
    needsReconciliation.value = true
    cancellationNotice.value = cause instanceof ApiError && cause.status === 409
      ? "La commande ne peut plus être annulée dans son statut courant. Vérification du détail..."
      : "Le résultat de l'annulation est incertain. Vérification du détail avant toute nouvelle tentative."
  } finally {
    if (controller === request) {
      cancelling.value = false
      if (needsReconciliation.value) await load()
    }
  }
}
watch([orderId, customerToken], () => {
  stopPolling()
  controller?.abort()
  controller = null
  order.value = null
  lastLoadedStatus.value = null
  statusNotification.value = null
  error.value = null
  loading.value = false
  cancelling.value = false
  confirming.value = false
  cancellationError.value = null
  cancellationNotice.value = ''
  needsReconciliation.value = false
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
      Retour à mes commandes
    </RouterLink>
    <h1>Détail de commande</h1>
  </header>
  <section
    class="card order-detail"
    aria-label="Détail de la commande"
    :aria-busy="loading || cancelling"
  >
    <div class="conversation-toolbar">
      <span class="eyebrow">Statut actuel</span>
      <button
        type="button"
        :disabled="loading || cancelling || !customerToken"
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
    <p
      v-if="cancellationNotice"
      role="status"
    >
      {{ cancellationNotice }}
    </p>
    <ApiErrorNotice :error="cancellationError" />
    <OrderStatusNotification
      v-if="statusNotification"
      :key="statusNotification.sequence"
      :status="statusNotification.status"
      @dismiss="statusNotification = null"
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
      <OrderTimeline
        :status="order.status"
        :events="order.events"
      />
      <button
        v-if="canCancel && !confirming"
        type="button"
        :disabled="loading || cancelling"
        @click="beginCancellation"
      >
        Annuler la commande
      </button>
      <section
        v-if="confirming"
        aria-label="Confirmation d'annulation"
        class="confirmation-card"
      >
        <p>Voulez-vous vraiment annuler cette commande ?</p>
        <div class="context-actions">
          <button
            type="button"
            :disabled="cancelling || loading"
            @click="confirmCancellation"
          >
            Confirmer l’annulation
          </button>
          <button
            type="button"
            :disabled="cancelling"
            @click="returnToDetail"
          >
            Retour
          </button>
        </div>
        <p
          v-if="cancelling"
          role="status"
        >
          Annulation en cours...
        </p>
      </section>
      <p class="hint">
        {{ ['DELIVERED', 'CANCELLED'].includes(order.status) ? 'Statut terminal : actualisation manuelle disponible.' : 'Actualisation automatique toutes les 15 secondes tant que cette page est ouverte.' }}
      </p>
    </div>
  </section>
</template>
