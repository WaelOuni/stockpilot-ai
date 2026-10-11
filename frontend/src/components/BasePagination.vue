<script setup lang="ts">
import { computed } from 'vue'
const props = withDefaults(defineProps<{ offset: number; limit?: number; total?: number; count?: number; hasMore?: boolean; loading?: boolean; label?: string; allowPageSize?: boolean }>(), { limit: 20, count: 0, label: 'résultats', total: undefined })
const emit = defineEmits<{ change: [offset: number]; size: [limit: number] }>()
const current = computed(() => Math.floor(props.offset / props.limit) + 1)
const pages = computed(() => props.total === undefined ? undefined : Math.ceil(props.total / props.limit))
const numbers = computed(() => pages.value === undefined ? [] : Array.from({ length: Math.min(5, pages.value) }, (_, index) => Math.max(1, Math.min(current.value - 2, pages.value! - 4)) + index))
const next = computed(() => props.hasMore ?? (props.total !== undefined ? props.offset + props.count < props.total : props.count >= props.limit))
function go(offset: number) { if (!props.loading) emit('change', Math.max(0, offset)) }
</script>
<template>
  <nav
    class="pagination"
    aria-label="Pagination"
  >
    <div
      class="pagination-info"
      aria-live="polite"
    >
      <span v-if="total !== undefined">Affichage de {{ count ? offset + 1 : 0 }} à {{ count ? offset + count : 0 }} sur {{ total }} {{ label }}</span>
      <span>Page {{ pages === 0 ? 0 : current }}<template v-if="pages !== undefined"> sur {{ pages }}</template></span>
    </div>
    <label
      v-if="allowPageSize && total !== undefined"
      class="page-size"
    >Par page
      <select
        aria-label="Par page"
        :value="limit"
        :disabled="loading"
        @change="emit('size', Number(($event.target as HTMLSelectElement).value))"
      >
        <option
          v-for="size in [10, 20, 50, 100]"
          :key="size"
          :value="size"
        >{{ size }}</option>
      </select>
    </label>
    <div class="pagination-controls">
      <button
        v-if="pages !== undefined"
        class="secondary"
        :disabled="loading || !offset"
        @click="go(0)"
      >
        Première page
      </button>
      <button
        class="secondary"
        :disabled="loading || !offset"
        @click="go(offset - limit)"
      >
        Précédent
      </button>
      <button
        v-for="number in numbers"
        :key="number"
        class="secondary page-number"
        :class="{ selected: number === current }"
        :aria-label="`Page ${number}`"
        :aria-current="number === current ? 'page' : undefined"
        :disabled="loading"
        @click="go((number - 1) * limit)"
      >
        {{ number }}
      </button>
      <button
        class="secondary"
        :disabled="loading || !next"
        @click="go(offset + limit)"
      >
        Suivant
      </button>
      <button
        v-if="pages !== undefined"
        class="secondary"
        :disabled="loading || !next || !pages"
        @click="go((pages! - 1) * limit)"
      >
        Dernière page
      </button>
    </div>
  </nav>
</template>
