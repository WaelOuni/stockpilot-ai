<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { getProduct } from '../api/client'
import { ApiError } from '../api/http'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import ProductInfo from '../components/ProductInfo.vue'
import type { ProductResponse } from '../types/api'
import { productCategoryLabels } from '../utils/products'

const route = useRoute()
const sku = computed(() => String(route.params.sku || ''))
const product = ref<ProductResponse | null>(null)
const loading = ref(false)
const error = ref<Error | null>(null)
const notFound = computed(() => error.value instanceof ApiError && error.value.status === 404)
let controller: AbortController | null = null
async function load() {
  if (loading.value || !sku.value) return
  const request = new AbortController()
  controller = request
  product.value = null
  loading.value = true
  error.value = null
  try {
    const result = await getProduct(sku.value, request.signal)
    if (!request.signal.aborted) product.value = result
  } catch (cause) {
    if (!request.signal.aborted) error.value = cause instanceof Error ? cause : new Error('Erreur inattendue.')
  } finally {
    if (controller === request) loading.value = false
  }
}
watch(sku, () => {
  controller?.abort()
  loading.value = false
  product.value = null
  void load()
}, { immediate: true })
onBeforeUnmount(() => { controller?.abort() })
function message(action: 'advice' | 'order'): string {
  const current = product.value
  if (!current) return ''
  if (action === 'order') return `Commande-moi 1 ${current.product_name}`
  const category = current.category === 'OTHER' ? 'cet outil' : productCategoryLabels[current.category].toLowerCase()
  return `Peux-tu me conseiller sur ${category} ${current.product_name} ?`
}
</script>

<template>
  <header class="page-heading">
    <RouterLink to="/products">
      Retour au catalogue
    </RouterLink>
    <h1>Fiche produit</h1>
  </header>
  <section
    class="card"
    aria-label="Fiche produit"
  >
    <div class="conversation-toolbar">
      <span>Informations produit</span><button
        type="button"
        :disabled="loading"
        @click="load"
      >
        Actualiser
      </button>
    </div>
    <p
      v-if="loading"
      class="empty-state"
      role="status"
    >
      Chargement du produit...
    </p>
    <p
      v-if="notFound"
      class="empty-state"
    >
      Produit introuvable.
    </p>
    <ApiErrorNotice
      v-else
      :error="error"
    />
    <div
      v-if="product"
      class="detail-content"
    >
      <ProductInfo :product="product" />
      <div class="context-actions">
        <RouterLink
          class="action-link"
          :to="{ path: '/assistant', query: { message: message('advice') } }"
        >
          Demander à StockPilot
        </RouterLink>
        <RouterLink
          class="action-link"
          :to="{ path: '/assistant', query: { message: message('order') } }"
        >
          Commander
        </RouterLink>
      </div>
      <p class="hint">
        Le message est prérempli, pas envoyé. Un JWT client est requis pour l’Assistant ; la disponibilité sera revérifiée avant confirmation.
      </p>
    </div>
  </section>
</template>
