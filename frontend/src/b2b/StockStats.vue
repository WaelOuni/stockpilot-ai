<script setup lang="ts">
import { t } from './preferences'
import type { Stock } from './contracts'
defineProps<{ stock?: Stock }>()
</script>
<template>
  <dl
    class="stock-stats"
    :aria-label="t('Stock')"
  >
    <div><dt>{{ t('Disponible') }}</dt><dd>{{ stock?.available ?? '—' }}</dd></div>
    <div><dt>{{ t('Réservé') }}</dt><dd>{{ stock?.reserved ?? '—' }}</dd></div>
    <div><dt>{{ t('Physique') }}</dt><dd>{{ stock?.quantity ?? '—' }}</dd></div>
  </dl>
</template>
<style scoped>
.stock-stats { display: flex; flex-wrap: wrap; gap: .5rem 1rem; margin: .5rem 0; }
.stock-stats div { display: flex; gap: .35rem; }
dd { margin: 0; font-weight: 700; font-variant-numeric: tabular-nums; }
</style>
