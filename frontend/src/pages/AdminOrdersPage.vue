<script setup lang="ts">
import DataTable from '../components/DataTable.vue'
import Pagination from "../components/BasePagination.vue"
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
const search = ref('')
let searchTimer: ReturnType<typeof setTimeout> | undefined
function applySearch() { clearTimeout(searchTimer); void router.push({ path: '/admin/orders', query: search.value.trim() ? { q: search.value.trim() } : {} }) }
function resetSearch() { search.value = ''; void router.push('/admin/orders') }
watch(search, () => { clearTimeout(searchTimer); if (search.value !== (route.query.q || '')) searchTimer = setTimeout(applySearch, 400) })
let controller: AbortController | undefined
async function load() {
  controller?.abort()
  page.value = null
  error.value = null
  if (offset.value === null) { error.value = new Error('Pagination invalide.'); loading.value = false; return }
  const request = new AbortController()
  controller = request
  loading.value = true
  try {
    if (route.query.q !== undefined && typeof route.query.q !== 'string') throw new Error('Recherche invalide.')
    const result = await listAdminOrders(offset.value, request.signal, typeof route.query.q === 'string' ? route.query.q : '')
    if (!request.signal.aborted && result.total !== undefined && offset.value > 0 && offset.value >= result.total) { navigate(Math.max(0, Math.floor((result.total - 1) / 20) * 20)); return }
    if (!request.signal.aborted) page.value = result
  }
  catch (cause) {
    if (!request.signal.aborted) {
      if (cause instanceof ApiError && cause.status === 401) await router.replace('/admin/login?reason=session')
      else error.value = cause instanceof Error ? cause : new Error('Chargement impossible.')
    }
  } finally { if (controller === request) loading.value = false }
}
watch(() => route.fullPath, () => { search.value = typeof route.query.q === 'string' ? route.query.q : ''; void load() }, { immediate: true })
onBeforeUnmount(() => { controller?.abort(); clearTimeout(searchTimer) })
function navigate(value: number) { void router.push({ path: '/admin/orders', query: { ...route.query, offset: value ? String(value) : undefined } }) }
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
  <form
    class="catalogue-filters"
    @submit.prevent="applySearch"
  >
    <label>Recherche commandes<input
      v-model="search"
      type="search"
      maxlength="100"
      placeholder="Numéro, produit ou identifiant client"
    /></label>
    <button
      type="submit"
      :disabled="loading"
    >
      Rechercher
    </button>
    <button
      type="button"
      class="secondary"
      @click="resetSearch"
    >
      Réinitialiser les filtres
    </button>
  </form>
  <template v-if="page">
    <p v-if="!page.items.length">
      Aucune commande sur cette page.
    </p>
    <DataTable
      v-if="page.items.length"
      label="Commandes administrateur"
    >
      <thead>
        <tr>
          <th scope="col">
            Commande / client
          </th><th scope="col">
            Produit
          </th><th scope="col">
            Quantité
          </th><th scope="col">
            Date
          </th><th scope="col">
            Statut
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="order in page.items"
          :key="order.order_id"
        >
          <td data-label="Commande / client">
            <RouterLink
              class="order-id"
              :to="`/admin/orders/${encodeURIComponent(order.order_id)}`"
            >
              {{ order.order_id }}
            </RouterLink><span class="cell-secondary">Client : {{ order.customer_id ?? 'Non renseigné (legacy)' }}</span>
          </td>
          <td data-label="Produit">
            <strong>{{ order.product_name }}</strong>
          </td>
          <td
            data-label="Quantité"
            class="numeric"
          >
            {{ order.quantity }}
          </td>
          <td data-label="Date">
            <time :datetime="order.created_at">{{ formatDate(order.created_at) }}</time>
          </td>
          <td data-label="Statut">
            <OrderStatusBadge :status="order.status" />
          </td>
        </tr>
      </tbody>
    </DataTable>
    <Pagination
      :offset="offset ?? 0"
      :total="page.total"
      :count="page.count"
      :has-more="page.has_more ?? page.count >= page.limit"
      :loading="loading"
      label="commandes"
      @change="navigate"
    />
  </template>
</template>
