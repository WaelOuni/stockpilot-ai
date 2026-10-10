<script setup lang="ts">
import type { ProductResponse } from '../types/api'
import { productCategoryLabels } from '../utils/products'
defineProps<{ product: ProductResponse }>()
const price = (value: number) => new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 2 }).format(value)
</script>

<template>
  <h2>{{ product.product_name }}</h2>
  <dl>
    <div><dt>Catégorie</dt><dd>{{ productCategoryLabels[product.category] }}</dd></div>
    <div><dt>Prix</dt><dd>{{ price(product.price) }}</dd></div>
    <div><dt>Stock</dt><dd>{{ product.stock }}</dd></div>
    <div><dt>Disponibilité</dt><dd>{{ product.stock > 0 ? 'Disponible' : 'Indisponible' }}</dd></div>
    <div>
      <dt>SKU</dt><dd class="order-id">
        {{ product.sku }}
      </dd>
    </div>
  </dl>
</template>
