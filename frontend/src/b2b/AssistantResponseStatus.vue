<script setup lang="ts">
import { t } from './preferences'
defineProps<{ waiting: boolean; slow: boolean; notice: string }>()
</script>
<template>
  <div
    class="assistant-response-status"
    role="status"
    aria-live="polite"
    aria-atomic="true"
  >
    <template v-if="waiting">
      <span
        class="response-dots"
        aria-hidden="true"
      >···</span>
      {{ t(slow ? 'La réponse prend un peu plus de temps. Ne renvoyez pas la demande.' : 'StockPilot prépare sa réponse…') }}
    </template>
    <template v-else-if="notice">
      {{ t(notice) }}
    </template>
  </div>
</template>
<style scoped>
.assistant-response-status { color:var(--muted, #475569); font-size:.9rem; min-height:1.5em; padding:8px 0; }
.response-dots { display:inline-block; font-size:1.4rem; font-weight:700; margin-right:6px; animation:response-pulse 1.3s ease-in-out infinite; }
@keyframes response-pulse { 50% { opacity:.35; } }
@media(prefers-reduced-motion:reduce) { .response-dots { animation:none; } }
</style>
