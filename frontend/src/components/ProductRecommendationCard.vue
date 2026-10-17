<script setup lang="ts">
import { RouterLink } from 'vue-router'
import type { ProductRecommendation } from '../types/api'
import { productCategoryLabels } from '../utils/products'
import { formatCatalogueNumber, formatPriceDifference } from '../utils/format'

defineProps<{ product: ProductRecommendation }>()
</script>

<template>
  <article
    class="card recommendation"
    :aria-label="product.role === 'reference' ? 'Produit de référence' : product.role === 'alternative' ? 'Alternative' : 'Produit recommandé'"
  >
    <div class="recommendation-heading">
      <span
        v-if="product.role"
        class="recommendation-role"
      >
        {{ product.role === 'reference' ? 'Produit de référence' : 'Alternative' }}
      </span>
      <span
        class="availability"
        :class="{ unavailable: product.stock === 0 }"
      >
        {{ product.stock > 0 ? 'Disponible' : 'Indisponible' }}
      </span>
    </div>
    <h2>{{ product.product_name }}</h2>
    <p
      v-if="product.matches_criteria === false"
      class="criteria-warning"
    >
      Hors critères demandés
    </p>
    <dl class="recommendation-facts">
      <div>
        <dt>Prix</dt><dd class="recommendation-price">
          {{ formatCatalogueNumber(product.price) }}
        </dd>
      </div>
      <div><dt>Stock</dt><dd>{{ product.stock }} {{ product.stock === 1 ? 'unité' : 'unités' }}</dd></div>
      <div><dt>Catégorie</dt><dd>{{ productCategoryLabels[product.category] }}</dd></div>
      <div>
        <dt>SKU</dt><dd class="order-id">
          {{ product.sku }}
        </dd>
      </div>
      <div v-if="product.role === 'alternative' && product.price_difference !== undefined">
        <dt>Écart avec la référence</dt>
        <dd>{{ product.price_difference > 0 ? '+' : '' }}{{ formatPriceDifference(product.price_difference) }}</dd>
      </div>
    </dl>
    <details class="recommendation-context">
      <summary>Pourquoi ce produit ?</summary>
      <p>{{ product.explanation }}</p>
    </details>
    <RouterLink
      class="context-link"
      :to="{ path: `/products/${encodeURIComponent(product.sku)}` }"
    >
      Voir la fiche catalogue<span class="sr-only"> de {{ product.product_name }}</span>
    </RouterLink>
  </article>
</template>

<style scoped>
.recommendation { min-width: 0; padding: 16px; overflow-wrap: anywhere; margin-block: 0.75rem; container-type: inline-size; }
.recommendation h2 { margin: 12px 0; font-size: 1.0625rem; }
.recommendation-heading { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.recommendation-role { color: var(--accent); font-size: .8125rem; font-weight: 650; }
.availability { padding: 3px 8px; border-radius: 8px; background: #e7f5ee; color: #166342; font-size: .8125rem; font-weight: 650; }
.availability.unavailable { background: #fff0f1; color: var(--danger); }
.recommendation-price { font-weight: 750; font-size: 1.125rem; }
.criteria-warning { color: var(--danger); font-weight: 650; }
.recommendation-context { margin-top: 16px; font-size: .8125rem; color: var(--muted); }
.recommendation-context summary { cursor: pointer; min-height: 32px; padding-block: 6px; }
.recommendation-facts > div { flex-wrap: wrap; }
.recommendation-facts dt { flex-shrink: 1; }
.recommendation-facts dd { max-width: 100%; }
@container (max-width: 220px) {
  .recommendation-facts > div { display: block; }
  .recommendation-facts dd { text-align: left; }
}
</style>
