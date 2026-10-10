<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { listCustomerOrders } from '../../api/client'
import ApiErrorNotice from '../../components/ApiErrorNotice.vue'
import type { CustomerOrdersResponse } from '../../types/api'
import { customerToken } from '../../utils/customerSession'
import { formatDate, orderStatusLabel } from '../../utils/format'

const limit = 20
const page = ref<CustomerOrdersResponse | null>(null)
const offset = ref(0)
const loading = ref(false)
const error = ref<Error | null>(null)
let controller: AbortController | null = null

async function load(nextOffset = offset.value) {
  controller?.abort()
  page.value = null
  error.value = null
  if (!customerToken.value) {
    loading.value = false
    return
  }
  const request = new AbortController()
  controller = request
  loading.value = true
  try {
    const result = await listCustomerOrders(limit, nextOffset, request.signal)
    if (request.signal.aborted) return
    page.value = result
    offset.value = nextOffset
  } catch (cause) {
    if (!request.signal.aborted) error.value = cause instanceof Error ? cause : new Error('Erreur inattendue.')
  } finally {
    if (controller === request) loading.value = false
  }
}

watch(customerToken, () => {
  offset.value = 0
  void load(0)
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { controller?.abort() })
</script>

<template>
  <section
    class="card orders-panel"
    aria-label="Historique des commandes"
  >
    <div class="conversation-toolbar">
      <span class="eyebrow">Vos commandes uniquement</span>
      <button
        type="button"
        class="secondary"
        :disabled="loading || !customerToken"
        @click="load()"
      >
        Actualiser
      </button>
    </div>
    <ApiErrorNotice :error="error" />
    <p
      v-if="!customerToken"
      class="empty-state"
    >
      Connectez un JWT client pour consulter vos commandes.
    </p>
    <p
      v-else-if="loading"
      class="empty-state"
      role="status"
    >
      Chargement des commandes...
    </p>
    <template v-else-if="page">
      <p
        v-if="!page.items.length"
        class="empty-state"
      >
        {{ offset === 0 ? 'Vous n’avez pas encore de commande.' : 'Aucune commande sur cette page.' }}
      </p>
      <div
        v-else
        class="order-grid"
      >
        <article
          v-for="order in page.items"
          :key="order.order_id"
          class="order-card"
        >
          <div class="order-heading">
            <h2>{{ order.product_name }}</h2>
            <span
              class="status-badge"
              :class="order.status.toLowerCase()"
            >{{ orderStatusLabel(order.status) }}</span>
          </div>
          <dl>
            <div><dt>Quantite</dt><dd>{{ order.quantity }}</dd></div>
            <div><dt>Date</dt><dd>{{ formatDate(order.created_at) }}</dd></div>
            <div>
              <dt>Commande</dt><dd class="order-id">
                {{ order.order_id }}
              </dd>
            </div>
          </dl>
        </article>
      </div>
    </template>
    <div
      v-if="customerToken"
      class="pagination"
    >
      <button
        type="button"
        class="secondary"
        :disabled="loading || offset === 0"
        @click="load(Math.max(0, offset - limit))"
      >
        Precedent
      </button>
      <span>Page {{ Math.floor(offset / limit) + 1 }}<template v-if="page"> · {{ page.count }} commande(s)</template></span>
      <button
        type="button"
        class="secondary"
        :disabled="loading || !page || page.count < limit"
        @click="load(offset + limit)"
      >
        Suivant
      </button>
    </div>
  </section>
</template>
