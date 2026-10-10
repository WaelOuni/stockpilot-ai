<script setup lang="ts">
import type { OrderStatus } from '../types/api'
import { orderStatusLabel } from '../utils/format'
defineProps<{ status: OrderStatus }>()
const steps: OrderStatus[] = ['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED']
</script>

<template>
  <section aria-label="Progression de la commande">
    <p
      v-if="status === 'CANCELLED'"
      class="cancelled-notice"
    >
      Commande annulée. Les étapes suivantes ne sont pas atteintes.
    </p>
    <ol
      v-else
      class="order-timeline"
    >
      <li
        v-for="(step, index) in steps"
        :key="step"
        :class="{ reached: index <= steps.indexOf(status) }"
        :aria-current="step === status ? 'step' : undefined"
      >
        <span>{{ orderStatusLabel(step) }}</span>
        <small>{{ step }}</small>
        <span class="sr-only">{{ index < steps.indexOf(status) ? 'Étape atteinte' : step === status ? 'Étape actuelle' : 'À venir' }}</span>
      </li>
    </ol>
  </section>
</template>
