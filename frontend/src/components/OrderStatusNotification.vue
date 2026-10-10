<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import type { OrderStatus } from '../types/api'
import { getOrderStatusMessage } from '../utils/orderStatusMessage'

defineProps<{ status: OrderStatus }>()
const emit = defineEmits<{ dismiss: [] }>()
let timer: ReturnType<typeof setTimeout> | undefined
onMounted(() => { timer = setTimeout(() => emit('dismiss'), 8_000) })
onBeforeUnmount(() => { clearTimeout(timer) })
</script>

<template>
  <div class="context-card">
    <p
      role="status"
      aria-atomic="true"
    >
      {{ getOrderStatusMessage(status) }}
    </p>
    <button
      type="button"
      aria-label="Fermer la notification de statut"
      @click="emit('dismiss')"
    >
      Fermer
    </button>
  </div>
</template>
