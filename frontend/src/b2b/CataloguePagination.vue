<script setup lang="ts">
import { computed } from 'vue'
import { t } from './preferences'
const props = defineProps<{ total: number; offset: number; limit: number; busy: boolean }>()
const emit = defineEmits<{ page: [page: number] }>()
const pages = computed(() => Math.ceil(props.total / props.limit))
const current = computed(() => Math.floor(props.offset / props.limit) + 1)
const lastReachable = computed(() => Math.min(pages.value, Math.floor(10000 / props.limit) + 1))
const links = computed(() => {
  if (!pages.value) return []
  const chosen = new Set([1, lastReachable.value])
  for (let n = Math.max(1, current.value - 1); n <= Math.min(lastReachable.value, current.value + 1); n++) chosen.add(n)
  if (current.value <= 2) chosen.add(Math.min(3, lastReachable.value))
  const result: (number | string)[] = []
  let previous = 0
  for (const n of [...chosen].filter(n => n > 0).sort((a, b) => a - b)) {
    if (n > previous + 1) result.push('gap-' + n)
    result.push(n); previous = n
  }
  return result
})
</script>
<template>
  <div class="catalogue-pagination">
    <p role="status">
      {{ total }} {{ t('produits • Page') }} {{ total ? current : 0 }} {{ t('sur') }} {{ pages }}
    </p>
    <p>{{ t('Affichage de') }} {{ total ? offset + 1 : 0 }} {{ t('à') }} {{ Math.min(offset + limit, total) }} {{ t('sur') }} {{ total }}</p>
    <nav
      :aria-label="t('Pages du catalogue')"
      class="b2b-actions"
    >
      <button
        class="secondary"
        :disabled="busy || current <= 1"
        @click="emit('page', 1)"
      >
        {{ t('Première') }}
      </button>
      <button
        class="secondary"
        :disabled="busy || current <= 1"
        @click="emit('page', current - 1)"
      >
        {{ t('Précédent') }}
      </button>
      <template
        v-for="link in links"
        :key="link"
      >
        <span
          v-if="typeof link === 'string'"
          aria-hidden="true"
        >…</span>
        <button
          v-else
          class="secondary"
          :disabled="busy"
          :aria-label="t('Page') + ' ' + link"
          :aria-current="link === current ? 'page' : undefined"
          @click="emit('page', link)"
        >
          {{ link }}
        </button>
      </template>
      <button
        class="secondary"
        :disabled="busy || current >= lastReachable"
        @click="emit('page', current + 1)"
      >
        {{ t('Suivant') }}
      </button>
      <button
        class="secondary"
        :disabled="busy || !pages || current >= pages || pages > lastReachable"
        @click="emit('page', pages)"
      >
        {{ t('Dernière') }}
      </button>
    </nav>
    <p
      v-if="pages > lastReachable"
      class="hint"
    >
      {{ t('Affinez les filtres pour accéder aux résultats au-delà de la limite de pagination.') }}
    </p>
  </div>
</template>
<style scoped>
.catalogue-pagination { margin:20px 0; }
.catalogue-pagination p { margin:6px 0; }
.catalogue-pagination nav { flex-wrap:wrap; gap:6px; margin-top:12px; }
.catalogue-pagination button { min-width:40px; min-height:44px; }
.catalogue-pagination [aria-current=page] { color:var(--brand); background:var(--soft); box-shadow:inset 0 -3px var(--brand); font-weight:700; }
</style>
