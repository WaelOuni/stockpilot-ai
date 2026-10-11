<script setup lang="ts">
import DataTable from '../components/DataTable.vue'
import ProductFilters from '../components/ProductFilters.vue'
import type { ProductCategory } from '../types/api'
import Pagination from "../components/BasePagination.vue"
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { listAdminProducts } from '../api/admin'
import { ApiError } from '../api/http'
import type { ProductsResponse } from '../types/api'
import AdminLogout from '../components/AdminLogout.vue'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import { productCategoryLabels } from '../utils/products'

const route = useRoute()
const router = useRouter()
const page = ref<ProductsResponse | null>(null)
const loading = ref(false)
const error = ref<Error | null>(null)
const offset = computed(() => {
  const raw = route.query.offset
  if (raw === undefined) return 0
  return typeof raw === 'string' && /^(0|[1-9]\d*)$/.test(raw) && Number.isSafeInteger(Number(raw))
    ? Number(raw) : null
})
const search = ref('')
const category = ref('')
const availability = ref('')
let searchTimer: ReturnType<typeof setTimeout> | undefined
function applyFilters() {
  clearTimeout(searchTimer)
  void router.push({ path: '/admin/products', query: { q: search.value.trim() || undefined, category: category.value || undefined, available: availability.value || undefined } })
}
function resetFilters() { search.value = category.value = availability.value = ''; void router.push('/admin/products') }
watch(search, () => { clearTimeout(searchTimer); if (search.value !== (route.query.q || '')) searchTimer = setTimeout(applyFilters, 400) })
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
    if (['q', 'category', 'available'].some(key => Array.isArray(route.query[key])) ||
        (route.query.category && !Object.hasOwn(productCategoryLabels, String(route.query.category))) ||
        (route.query.available && !['true', 'false'].includes(String(route.query.available)))) throw new Error('Filtres invalides dans l’URL.')
    const result = await listAdminProducts(offset.value, request.signal, { q: typeof route.query.q === 'string' ? route.query.q : undefined, category: route.query.category as ProductCategory | undefined, available: route.query.available === 'true' ? true : route.query.available === 'false' ? false : undefined })
    if (!request.signal.aborted && result.total !== undefined && offset.value > 0 && offset.value >= result.total) { navigate(Math.max(0, Math.floor((result.total - 1) / 20) * 20)); return }
    if (!request.signal.aborted) page.value = result
  } catch (cause) {
    if (!request.signal.aborted) {
      if (cause instanceof ApiError && cause.status === 401) await router.replace('/admin/login?reason=session')
      else error.value = cause instanceof Error ? cause : new Error('Chargement impossible.')
    }
  } finally { if (controller === request) loading.value = false }
}
function navigate(value: number) {
  void router.push({ path: '/admin/products', query: { ...route.query, offset: value ? String(value) : undefined } })
}
watch(() => route.fullPath, () => {
  search.value = typeof route.query.q === 'string' ? route.query.q : ''
  category.value = typeof route.query.category === 'string' ? route.query.category : ''
  availability.value = typeof route.query.available === 'string' ? route.query.available : ''
  void load()
}, { immediate: true })
onBeforeUnmount(() => { controller?.abort(); clearTimeout(searchTimer) })
</script>
<template>
  <header class="page-heading">
    <span class="eyebrow">Back-office</span>
    <h1>Produits administrateur</h1>
  </header>
  <AdminLogout />
  <RouterLink to="/admin/products/new">
    Créer un produit
  </RouterLink>
  <RouterLink to="/admin/products/import">
    Importer des produits CSV
  </RouterLink>
  <button
    :disabled="loading"
    @click="load"
  >
    Actualiser
  </button>
  <p class="hint">
    Stock en lecture seule ici. Les ajustements audités utilisent une page dédiée ; aucun retrait de produit.
  </p>
  <p
    v-if="loading"
    role="status"
  >
    Chargement des produits…
  </p>
  <ApiErrorNotice
    v-if="error"
    :error="error"
  />
  <ProductFilters
    v-model:search="search"
    v-model:category="category"
    v-model:availability="availability"
    :loading="loading"
    @apply="applyFilters"
    @reset="resetFilters"
  />
  <template v-if="page">
    <p v-if="!page.items.length">
      Aucun produit sur cette page.
    </p>
    <DataTable
      v-if="page.items.length"
      label="Produits administrateur"
    >
      <thead>
        <tr>
          <th scope="col">
            Produit
          </th><th scope="col">
            Catégorie
          </th><th scope="col">
            Prix
          </th><th scope="col">
            Stock
          </th><th scope="col">
            Actions
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="product in page.items"
          :key="product.sku"
        >
          <td data-label="Produit">
            <strong>{{ product.product_name }}</strong><span class="cell-secondary order-id">{{ product.sku }}</span>
          </td>
          <td data-label="Catégorie">
            {{ productCategoryLabels[product.category] }}
          </td>
          <td
            data-label="Prix"
            class="numeric"
          >
            {{ product.price.toLocaleString('fr-FR', { maximumFractionDigits: 2 }) }}
          </td>
          <td
            data-label="Stock"
            class="numeric"
          >
            {{ product.stock }} <span class="cell-secondary">lecture seule</span>
          </td>
          <td data-label="Actions">
            <div class="row-actions">
              <RouterLink :to="`/admin/products/${encodeURIComponent(product.sku)}/edit`">
                Modifier {{ product.product_name }}
              </RouterLink>
              <RouterLink :to="`/admin/products/${encodeURIComponent(product.sku)}/stock`">
                Ajuster le stock de {{ product.product_name }}
              </RouterLink>
            </div>
          </td>
        </tr>
      </tbody>
    </DataTable>
    <Pagination
      :offset="offset ?? 0"
      :total="page.total"
      :count="page.count"
      :has-more="page.has_more"
      :loading="loading"
      label="produits"
      @change="navigate"
    />
  </template>
</template>
