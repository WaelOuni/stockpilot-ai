<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { listProducts } from '../api/client'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import ProductInfo from '../components/ProductInfo.vue'
import type { ProductsResponse } from '../types/api'

const page = ref<ProductsResponse | null>(null)
const offset = ref(0)
const loading = ref(false)
const error = ref<Error | null>(null)
let controller: AbortController | null = null
async function load(next = offset.value) {
  if (loading.value) return
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = null
  page.value = null
  try {
    const result = await listProducts(20, next, request.signal)
    if (request.signal.aborted) return
    page.value = result
    offset.value = next
  } catch (cause) {
    if (!request.signal.aborted) error.value = cause instanceof Error ? cause : new Error('Erreur inattendue.')
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}
onMounted(() => { void load() })
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
        Aucun produit sur cette page.
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
        @click="load(Math.max(0, offset - 20))"
      >
        Précédent
      </button>
      <span>Page {{ Math.floor(offset / 20) + 1 }}</span>
      <button
        type="button"
        class="secondary"
        :disabled="loading || !page || page.count < 20"
        @click="load(offset + 20)"
      >
        Suivant
      </button>
    </div>
  </section>
</template>
