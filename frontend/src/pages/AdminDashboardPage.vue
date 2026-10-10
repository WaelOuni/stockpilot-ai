<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { getAdminDashboard } from '../api/admin'
import { ApiError } from '../api/http'
import type { BackofficeDashboard } from '../types/api'
import AdminLogout from '../components/AdminLogout.vue'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import { formatDate, orderStatusLabel } from '../utils/format'

const router = useRouter()
const dashboard = ref<BackofficeDashboard | null>(null)
const loading = ref(false)
const loggingOut = ref(false)
const error = ref<Error | null>(null)
let requestController: AbortController | undefined
let generation = 0

async function refresh() {
  requestController?.abort()
  const request = new AbortController()
  requestController = request
  const currentGeneration = ++generation
  dashboard.value = null
  error.value = null
  loading.value = true
  try {
    const result = await getAdminDashboard(request.signal)
    if (!request.signal.aborted && generation === currentGeneration) dashboard.value = result
  } catch (cause) {
    if (!request.signal.aborted && generation === currentGeneration) {
      if (cause instanceof ApiError && cause.status === 401) {
        await router.replace('/admin/login?reason=session')
      } else {
        error.value = cause instanceof Error ? cause : new Error('Chargement du tableau de bord impossible.')
      }
    }
  } finally {
    if (requestController === request && generation === currentGeneration) loading.value = false
  }
}

function logoutPending(pending: boolean) {
  loggingOut.value = pending
  if (pending) {
    generation++
    requestController?.abort()
    loading.value = false
    dashboard.value = null
  }
}

onMounted(() => { void refresh() })
onBeforeUnmount(() => {
  generation++
  requestController?.abort()
})
</script>

<template>
  <header class="page-heading">
    <span class="eyebrow">Back-office · Lecture seule</span>
    <h1>Tableau de bord</h1>
    <p>Instantané opérationnel des commandes et du stock enregistré.</p>
  </header>
  <AdminLogout @pending="logoutPending" />
  <div class="dashboard-toolbar">
    <p class="hint">
      Les indicateurs proviennent de lectures indépendantes et peuvent refléter des instants légèrement différents.
    </p>
    <button
      class="secondary"
      :disabled="loading || loggingOut"
      @click="refresh"
    >
      {{ loading ? 'Actualisation…' : 'Actualiser' }}
    </button>
  </div>
  <p
    v-if="loading"
    role="status"
  >
    Chargement du tableau de bord…
  </p>
  <ApiErrorNotice
    v-if="error"
    :error="error"
  />
  <template v-if="dashboard">
    <section
      class="dashboard-section"
      aria-labelledby="dashboard-orders-heading"
    >
      <div class="dashboard-section-heading">
        <h2 id="dashboard-orders-heading">
          Commandes
        </h2>
        <RouterLink to="/admin/orders">
          Voir les commandes
        </RouterLink>
      </div>
      <dl class="dashboard-metrics">
        <div class="dashboard-metric dashboard-metric-primary">
          <dt>Total des commandes</dt>
          <dd>{{ dashboard.order_stats.total_orders }}</dd>
        </div>
        <div
          v-for="status in ['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELLED'] as const"
          :key="status"
          class="dashboard-metric"
        >
          <dt>{{ orderStatusLabel(status) }}</dt>
          <dd>{{ dashboard.order_stats.by_status[status] }}</dd>
        </div>
      </dl>
    </section>

    <section
      class="dashboard-section"
      aria-labelledby="dashboard-products-heading"
    >
      <div class="dashboard-section-heading">
        <h2 id="dashboard-products-heading">
          Produits et stock
        </h2>
        <RouterLink to="/admin/products">
          Voir le catalogue
        </RouterLink>
      </div>
      <dl class="dashboard-metrics dashboard-product-metrics">
        <div class="dashboard-metric dashboard-metric-primary">
          <dt>Total des produits</dt>
          <dd>{{ dashboard.product_stats.total_products }}</dd>
        </div>
        <div class="dashboard-metric">
          <dt>Disponibles (stock supérieur à 0)</dt>
          <dd>{{ dashboard.product_stats.available_products }}</dd>
        </div>
        <div class="dashboard-metric">
          <dt>Indisponibles (stock inférieur ou égal à 0)</dt>
          <dd>{{ dashboard.product_stats.unavailable_products }}</dd>
        </div>
        <div class="dashboard-metric">
          <dt>Unités de stock au total</dt>
          <dd>{{ dashboard.product_stats.total_stock_units }}</dd>
        </div>
      </dl>
      <p class="hint">
        Le stock total additionne des unités de produits différents ; aucun seuil de stock faible n’est défini.
      </p>
    </section>

    <div class="dashboard-sections">
      <section
        class="dashboard-section"
        aria-labelledby="dashboard-recent-orders-heading"
      >
        <div class="dashboard-section-heading">
          <h2 id="dashboard-recent-orders-heading">
            Commandes récentes
          </h2>
          <RouterLink to="/admin/orders">
            Toutes les commandes
          </RouterLink>
        </div>
        <p
          v-if="!dashboard.recent_orders.length"
          class="dashboard-empty"
        >
          Aucune commande enregistrée.
        </p>
        <ol
          v-else
          class="dashboard-list"
        >
          <li
            v-for="order in dashboard.recent_orders"
            :key="order.order_id"
            class="context-card"
          >
            <RouterLink
              class="order-id"
              :to="`/admin/orders/${encodeURIComponent(order.order_id)}`"
            >
              {{ order.order_id }}
            </RouterLink>
            <p>{{ order.product_name }} · Quantité : {{ order.quantity }}</p>
            <p>Statut : {{ orderStatusLabel(order.status) }}</p>
            <time :datetime="order.created_at">{{ formatDate(order.created_at) }}</time>
          </li>
        </ol>
      </section>

      <section
        class="dashboard-section"
        aria-labelledby="dashboard-recent-stock-heading"
      >
        <div class="dashboard-section-heading">
          <h2 id="dashboard-recent-stock-heading">
            Ajustements de stock récents
          </h2>
        </div>
        <p
          v-if="!dashboard.recent_stock_movements.length"
          class="dashboard-empty"
        >
          Aucun ajustement de stock enregistré.
        </p>
        <ol
          v-else
          class="dashboard-list"
        >
          <li
            v-for="(movement, index) in dashboard.recent_stock_movements"
            :key="`${movement.product_sku}-${movement.occurred_at}-${index}`"
            class="context-card"
          >
            <p class="dashboard-movement-delta">
              {{ movement.delta > 0 ? '+' : '' }}{{ movement.delta }}
              · SKU {{ movement.product_sku }}
            </p>
            <p>{{ movement.reason }}</p>
            <time :datetime="movement.occurred_at">{{ formatDate(movement.occurred_at) }}</time>
            <RouterLink :to="`/admin/products/${encodeURIComponent(movement.product_sku)}/stock`">
              Voir l’historique complet du stock
            </RouterLink>
          </li>
        </ol>
        <p class="hint">
          Affichage des cinq derniers ajustements, pas de l’historique complet.
        </p>
      </section>
    </div>
  </template>
</template>
