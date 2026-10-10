<script setup lang="ts">
import { RouterLink } from 'vue-router'
import type { OrderStatus } from '../types/api'
import OrderStatusBadge from './OrderStatusBadge.vue'
defineProps<{ title: string; orderId: string; status?: OrderStatus; productName?: string; quantity?: number }>()
</script>

<template>
  <section
    class="context-card"
    :aria-label="title"
  >
    <h3>{{ title }}</h3>
    <p
      v-if="productName"
      class="context-product"
    >
      {{ productName }}
    </p>
    <p v-if="quantity !== undefined">
      Quantité : {{ quantity }}
    </p>
    <p>Commande : <span class="order-id">{{ orderId }}</span></p>
    <p v-if="status">
      Statut : <OrderStatusBadge :status="status" />
    </p>
    <slot></slot>
    <RouterLink
      class="context-link"
      :to="`/orders/${encodeURIComponent(orderId)}`"
    >
      Voir la commande
    </RouterLink>
    <RouterLink
      class="context-link"
      to="/orders"
    >
      Voir mes commandes
    </RouterLink>
  </section>
</template>
