<script setup lang="ts">
import ProductFilters from '../components/ProductFilters.vue'
import Pagination from "../components/BasePagination.vue"
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { listProducts, type ProductFilters as ProductFilterCriteria } from '../api/client'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import ProductInfo from '../components/ProductInfo.vue'
import type { ProductsResponse, ProductCategory } from '../types/api'
import { catalogueQuery, productCategoryLabels } from '../utils/products'

const route = useRoute()
const router = useRouter()
const catalogueContext = computed(() => catalogueQuery(route.query))
const page = ref<ProductsResponse | null>(null)
const offset = computed(() => Number(route.query.offset || 0))
const limit = computed(() => Number(route.query.limit || 20))
const search = ref('')
const category = ref('')
const availability = ref('')
const filtered = computed(() => Boolean(route.query.q || route.query.category || route.query.available))
const loading = ref(false)
const error = ref<Error | null>(null)
let controller: AbortController | null = null
let searchTimer: ReturnType<typeof setTimeout> | undefined
watch(search, () => { clearTimeout(searchTimer); if (search.value !== (route.query.q || '')) searchTimer = setTimeout(applyFilters, 400) })
function filters(): ProductFilterCriteria {
  const categoryValue = route.query.category
  return {
    q: typeof route.query.q === 'string' ? route.query.q : undefined,
    category: typeof categoryValue === 'string' && Object.hasOwn(productCategoryLabels, categoryValue)
      ? categoryValue as ProductCategory : undefined,
    available: route.query.available === 'true' ? true : route.query.available === 'false' ? false : undefined,
  }
}
function applyFilters() {
  clearTimeout(searchTimer)
  const query: Record<string, string> = {}
  if (search.value.trim()) query.q = search.value.trim()
  if (category.value) query.category = category.value
  if (availability.value) query.available = availability.value
  if (limit.value !== 20) query.limit = String(limit.value)
  void router.push({ path: '/products', query })
}
function paginate(next: number) {
  void router.push({ path: '/products', query: { ...route.query, offset: next ? String(next) : undefined } })
}
function changeSize(size: number) { void router.push({ path: '/products', query: { ...route.query, offset: undefined, limit: size === 20 ? undefined : String(size) } }) }
function resetFilters() {
  search.value = ''
  category.value = ''
  availability.value = ''
  void router.push('/products')
}
async function load() {
  if (loading.value) return
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = null
  page.value = null
  try {
    if (['q', 'category', 'available', 'offset', 'limit'].some((key) => Array.isArray(route.query[key]))) {
      throw new Error('Filtres répétés invalides dans l’URL.')
    }
    if (![10, 20, 50, 100].includes(limit.value)) throw new Error('Taille de page invalide dans l’URL.')
    if (!Number.isSafeInteger(offset.value) || offset.value < 0) throw new Error('Pagination invalide dans l’URL.')
    if (route.query.category && !Object.hasOwn(productCategoryLabels, String(route.query.category))) {
      throw new Error('Catégorie invalide dans l’URL.')
    }
    if (route.query.available && !['true', 'false'].includes(String(route.query.available))) {
      throw new Error('Disponibilité invalide dans l’URL.')
    }
    const result = await listProducts(limit.value, offset.value, request.signal, filters())
    if (request.signal.aborted) return
    if (result.total !== undefined && offset.value > 0 && offset.value >= result.total) {
      paginate(Math.max(0, Math.floor((result.total - 1) / limit.value) * limit.value))
      return
    }
    page.value = result
  } catch (cause) {
    if (!request.signal.aborted) error.value = cause instanceof Error ? cause : new Error('Erreur inattendue.')
  } finally {
    if (controller === request) loading.value = false
  }
}
watch(() => route.fullPath, () => {
  controller?.abort()
  loading.value = false
  search.value = typeof route.query.q === 'string' ? route.query.q : ''
  category.value = typeof route.query.category === 'string' ? route.query.category : ''
  availability.value = typeof route.query.available === 'string' ? route.query.available : ''
  void load()
}, { immediate: true })
onBeforeUnmount(() => { controller?.abort(); clearTimeout(searchTimer) })
</script>

<template>
  <header class="page-heading">
    <span class="eyebrow">Outillage disponible</span>
    <h1>Catalogue</h1>
    <p>Parcourez les produits, puis poursuivez avec StockPilot.</p>
  </header>
  <section
    class="card"
    aria-label="Catalogue produits"
  >
    <div class="conversation-toolbar">
      <span>Produits</span><button
        type="button"
        :disabled="loading"
        @click="load()"
      >
        Actualiser
      </button>
    </div>
    <ProductFilters
      v-model:search="search"
      v-model:category="category"
      v-model:availability="availability"
      :loading="loading"
      @apply="applyFilters"
      @reset="resetFilters"
    />
    <ApiErrorNotice :error="error" />
    <p
      v-if="loading"
      class="empty-state"
      role="status"
    >
      Chargement du catalogue...
    </p>
    <template v-if="page">
      <p
        v-if="!page.items.length"
        class="empty-state"
      >
        {{ filtered ? 'Aucun produit ne correspond à vos critères.' : 'Aucun produit sur cette page.' }}
      </p>
      <div
        v-else
        class="order-grid"
      >
        <article
          v-for="product in page.items"
          :key="product.sku"
          class="order-card"
        >
          <ProductInfo :product="product" />
          <RouterLink
            class="context-link"
            :to="{ path: `/products/${encodeURIComponent(product.sku)}`, query: catalogueContext }"
          >
            Voir le produit {{ product.product_name }}
          </RouterLink>
        </article>
      </div>
    </template>
    <Pagination
      :offset="offset"
      :limit="limit"
      allow-page-size
      :total="page?.total"
      :count="page?.count"
      :has-more="page?.has_more ?? false"
      :loading="loading"
      label="produits"
      @size="changeSize"
      @change="paginate"
    />
  </section>
</template>
