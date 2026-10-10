<script setup lang="ts">
import type { OrderStatus } from '../types/api'
import { orderStatusLabel } from '../utils/format'
defineProps<{ status: OrderStatus }>()
</script>

<template>
  <span
    class="status-badge"
    :class="status.toLowerCase()"
  >
    {{ orderStatusLabel(status) }} <span class="status-code">({{ status }})</span>
  </span>
</template>
