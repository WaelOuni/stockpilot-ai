<script setup lang="ts">
import { RouterLink } from 'vue-router'
import ProductInfo from './ProductInfo.vue'
import type { ProductRecommendation } from '../types/api'

defineProps<{ product: ProductRecommendation }>()
</script>

<template>
  <article
    class="card recommendation"
    :aria-label="product.role === 'reference' ? 'Produit de référence' : product.role === 'alternative' ? 'Alternative' : 'Produit recommandé'"
  >
    <p v-if="product.role">
      <strong>{{ product.role === 'reference' ? 'Produit de référence' : 'Alternative' }}</strong>
      <span v-if="product.matches_criteria === false"> — Hors critères demandés</span>
    </p>
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
