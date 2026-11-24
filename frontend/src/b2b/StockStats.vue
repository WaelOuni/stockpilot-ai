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
  <p
    v-if="stock && stock.available === 0"
    class="stock-signal"
    data-level="empty"
  >
    {{ t('Rupture de stock disponible') }}
  </p>
  <p
    v-else-if="stock && stock.available <= 5"
    class="stock-signal"
    data-level="low"
  >
    {{ t('Stock faible : 1 à 5 unités disponibles (seuil indicatif).') }}
  </p>
  <p
    v-else-if="!stock"
    class="hint"
  >
    {{ t('Disponibilité non renseignée') }}
  </p>
</template>
<style scoped>
.stock-stats { display: grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin:16px 0; }
.stock-stats div { display:grid; gap:4px; padding:10px; background:var(--soft); border-radius:8px; }
dt { font-size:.75rem; color:var(--muted); }
dd { margin:0; font-size:1.15rem; font-weight:700; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.stock-signal { font-size:.8rem; font-weight:600; padding:8px 10px; border-left:3px solid var(--warning); background:var(--soft); border-radius:4px; }
.stock-signal[data-level=empty] { color:var(--danger-ink); border-color:var(--danger-ink); }
</style>
