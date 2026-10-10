<script setup lang="ts">
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
    const result = await listAdminProducts(offset.value, request.signal)
    if (!request.signal.aborted) page.value = result
  } catch (cause) {
    if (!request.signal.aborted) {
      if (cause instanceof ApiError && cause.status === 401) await router.replace('/admin/login?reason=session')
      else error.value = cause instanceof Error ? cause : new Error('Chargement impossible.')
    }
  } finally { if (controller === request) loading.value = false }
}
function navigate(value: number) {
  void router.push({ path: '/admin/products', query: value ? { offset: String(value) } : {} })
}
watch(offset, () => { void load() }, { immediate: true })
onBeforeUnmount(() => controller?.abort())
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
  <template v-if="page">
    <p v-if="!page.items.length">
      Aucun produit sur cette page.
    </p>
    <article
      v-for="product in page.items"
      :key="product.sku"
      class="context-card"
    >
      <h2>{{ product.product_name }}</h2>
      <p>SKU : {{ product.sku }} · {{ productCategoryLabels[product.category] }}</p>
      <p>Prix : {{ product.price.toLocaleString('fr-FR') }} · Stock : {{ product.stock }} (lecture seule)</p>
      <RouterLink :to="`/admin/products/${encodeURIComponent(product.sku)}/stock`">
        Ajuster le stock de {{ product.product_name }}
      </RouterLink>
      <RouterLink :to="`/admin/products/${encodeURIComponent(product.sku)}/edit`">
        Modifier {{ product.product_name }}
      </RouterLink>
    </article>
    <nav
      class="pagination"
      aria-label="Pagination produits administrateur"
    >
      <button
        :disabled="loading || !offset"
        @click="navigate(Math.max(0, (offset ?? 0) - 20))"
      >
        Précédent
      </button>
      <span>Page {{ (offset ?? 0) / 20 + 1 }}</span>
      <button
        :disabled="loading || !page.has_more"
        @click="navigate((offset ?? 0) + 20)"
      >
        Suivant
      </button>
    </nav>
  </template>
</template>
