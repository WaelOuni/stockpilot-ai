<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { listProducts, type ProductFilters } from '../api/client'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import ProductInfo from '../components/ProductInfo.vue'
import type { ProductsResponse, ProductCategory } from '../types/api'
import { productCategoryLabels } from '../utils/products'

const route = useRoute()
const router = useRouter()
const page = ref<ProductsResponse | null>(null)
const offset = computed(() => Number(route.query.offset || 0))
const search = ref('')
const category = ref('')
const availability = ref('')
const filtered = computed(() => Boolean(route.query.q || route.query.category || route.query.available))
const loading = ref(false)
const error = ref<Error | null>(null)
let controller: AbortController | null = null
function filters(): ProductFilters {
  const categoryValue = route.query.category
  return {
    q: typeof route.query.q === 'string' ? route.query.q : undefined,
    category: typeof categoryValue === 'string' && Object.hasOwn(productCategoryLabels, categoryValue)
      ? categoryValue as ProductCategory : undefined,
    available: route.query.available === 'true' ? true : route.query.available === 'false' ? false : undefined,
  }
}
function applyFilters() {
  const query: Record<string, string> = {}
  if (search.value.trim()) query.q = search.value.trim()
  if (category.value) query.category = category.value
  if (availability.value) query.available = availability.value
  void router.push({ path: '/products', query })
}
function paginate(next: number) {
  void router.push({ path: '/products', query: { ...route.query, offset: next ? String(next) : undefined } })
}
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
    if (['q', 'category', 'available', 'offset'].some((key) => Array.isArray(route.query[key]))) {
      throw new Error('Filtres répétés invalides dans l’URL.')
    }
    if (!Number.isSafeInteger(offset.value) || offset.value < 0) throw new Error('Pagination invalide dans l’URL.')
    if (route.query.category && !Object.hasOwn(productCategoryLabels, String(route.query.category))) {
      throw new Error('Catégorie invalide dans l’URL.')
    }
    if (route.query.available && !['true', 'false'].includes(String(route.query.available))) {
      throw new Error('Disponibilité invalide dans l’URL.')
    }
    const result = await listProducts(20, offset.value, request.signal, filters())
    if (request.signal.aborted) return
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
onBeforeUnmount(() => { controller?.abort() })
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
    <form
      class="catalogue-filters"
      @submit.prevent="applyFilters"
    >
      <label>Recherche
        <input
          v-model="search"
          type="search"
          maxlength="100"
          placeholder="Nom ou SKU"
        />
      </label>
      <label>Catégorie
        <select
          v-model="category"
          aria-label="Catégorie"
        >
          <option value="">Toutes</option>
          <option
            v-for="(label, value) in productCategoryLabels"
            :key="value"
            :value="value"
          >{{ label }}</option>
        </select>
      </label>
      <label>Disponibilité
        <select
          v-model="availability"
          aria-label="Disponibilité"
        >
          <option value="">Tous</option>
          <option value="true">Disponibles</option>
          <option value="false">Indisponibles</option>
        </select>
      </label>
      <button
        type="submit"
        :disabled="loading"
      >
        Rechercher
      </button>
      <button
        type="button"
        class="secondary"
        :disabled="loading"
        @click="resetFilters"
      >
        Réinitialiser les filtres
      </button>
    </form>
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
            :to="`/products/${encodeURIComponent(product.sku)}`"
          >
            Voir le produit
          </RouterLink>
        </article>
      </div>
    </template>
    <div class="pagination">
      <button
        type="button"
        class="secondary"
        :disabled="loading || offset === 0"
        @click="paginate(Math.max(0, offset - 20))"
      >
        Précédent
      </button>
      <span>Page {{ Math.floor(offset / 20) + 1 }}</span>
      <button
        type="button"
        class="secondary"
        :disabled="loading || !page || page.count < 20"
        @click="paginate(offset + 20)"
      >
        Suivant
      </button>
    </div>
  </section>
</template>
