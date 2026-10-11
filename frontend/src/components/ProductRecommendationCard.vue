<script setup lang="ts">
import { RouterLink } from 'vue-router'
import ProductInfo from './ProductInfo.vue'
import type { ProductRecommendation } from '../types/api'

defineProps<{ product: ProductRecommendation }>()
</script>

<template>
  <article
    class="card recommendation"
    aria-label="Produit recommandé"
  >
    <ProductInfo :product="product" />
    <p>{{ product.explanation }}</p>
    <RouterLink :to="{ path: `/products/${encodeURIComponent(product.sku)}` }">
      Voir la fiche de {{ product.product_name }}
    </RouterLink>
  </article>
</template>

<style scoped>
.recommendation { min-width: 0; padding: 16px; overflow-wrap: anywhere; margin-block: 0.75rem; }
</style>
