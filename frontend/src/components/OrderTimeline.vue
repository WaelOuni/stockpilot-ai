<script setup lang="ts">
import type { OrderEvent, OrderStatus } from '../types/api'
import { formatDate, orderStatusLabel } from '../utils/format'
defineProps<{ status: OrderStatus; events?: OrderEvent[] }>()
</script>

<template>
  <section aria-label="Progression de la commande">
    <p
      v-if="status === 'CANCELLED'"
      class="cancelled-notice"
    >
      Commande annulée.
    </p>
    <ol
      v-if="events?.length"
      class="order-timeline"
    >
      <li
        v-for="(event, index) in events"
        :key="index"
        class="reached"
        :aria-current="index === events.length - 1 ? 'step' : undefined"
      >
        <span>{{ orderStatusLabel(event.status) }}</span>
        <small>{{ event.status }}</small>
        <time :datetime="event.occurred_at">{{ formatDate(event.occurred_at) }}</time>
        <small>Source : {{ event.source }}</small>
      </li>
    </ol>
    <p
      v-else
      class="hint"
    >
      Statut actuel : {{ orderStatusLabel(status) }} ({{ status }}).
      L’historique détaillé n’est pas disponible.
    </p>
  </section>
</template>
