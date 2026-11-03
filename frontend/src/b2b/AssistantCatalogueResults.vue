<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useB2B } from './session'
import { assistantResults } from './assistant'
import { StaleResponse } from './client'
import { t, dateLabel } from './preferences'
import { formatMoney } from './money'
import CataloguePagination from './CataloguePagination.vue'

const props = defineProps<{ conversationId: string; turnId: string }>()
const session = useB2B()
const opened = ref(false), busy = ref(false), error = ref('')
const result = ref<ReturnType<typeof assistantResults> | null>(null)
const minimum = ref(''), maximum = ref(''), exclude = ref(false), sort = ref('available')
let epoch = 0
const identity = computed(() => [session.current.value?.organization_id, session.current.value?.user_id,
  session.current.value?.csrf_token, props.conversationId, props.turnId].join(':'))
function clear() { epoch++; opened.value = false; result.value = null; error.value = ''; busy.value = false }
watch(identity, clear, { flush: 'sync' })
onUnmounted(clear)
function bound(value: string) {
  if (!value.trim()) return undefined
  if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))) throw new Error('Les bornes de stock doivent être des entiers positifs ou nuls.')
  return Number(value)
}
async function load(page = 1, custom = true) {
  const version = ++epoch
  busy.value = true; error.value = ''; result.value = null
  try {
    const query = new URLSearchParams({ limit: '20', offset: String((page - 1) * 20) })
    if (custom) {
      const low = bound(minimum.value), high = bound(maximum.value)
      if (low !== undefined && high !== undefined && low > high) throw new Error('La borne minimale dépasse la borne maximale.')
      query.set('custom_stock_range', 'true')
      if (low !== undefined) query.set('minimum_available', String(low))
      if (high !== undefined) query.set('maximum_available', String(high))
      query.set('exclude_sold_out', String(exclude.value)); query.set('sort', sort.value)
    }
    const value = await session.client.request('/b2b/api/assistant/conversations/' + encodeURIComponent(props.conversationId)
      + '/turns/' + encodeURIComponent(props.turnId) + '/products?' + query, assistantResults)
    if (version !== epoch) throw new StaleResponse()
    result.value = value
    minimum.value = value.filters.minimum_available?.toString() ?? ''
    maximum.value = value.filters.maximum_available?.toString() ?? ''
    exclude.value = value.filters.exclude_sold_out; sort.value = value.filters.sort
  } catch (e) {
    if (version === epoch && !(e instanceof StaleResponse)) error.value = e instanceof Error ? e.message : 'Action impossible.'
  } finally { if (version === epoch) busy.value = false }
}
function open() { opened.value = true; void load(1, false) }
</script>
<template>
  <section
    class="current-results"
    :aria-label="t('Résultats actualisés')"
  >
    <button
      v-if="!opened"
      class="secondary"
      @click="open"
    >
      {{ t('Voir plus de produits') }}
    </button>
    <template v-else>
      <h2>{{ t('Résultats actualisés') }}</h2>
      <p class="hint">
        {{ t('Cette liste utilise le stock actuel. Les cartes du message initial restent inchangées.') }}
      </p>
      <form
        class="b2b-actions"
        @submit.prevent="load()"
      >
        <label>{{ t('Disponible minimum') }}<input
          v-model="minimum"
          inputmode="numeric"
          :disabled="busy"
        /></label>
        <label>{{ t('Disponible maximum') }}<input
          v-model="maximum"
          inputmode="numeric"
          :disabled="busy"
        /></label>
        <label>{{ t('Tri des résultats') }}<select
          v-model="sort"
          :disabled="busy"
        >
          <option value="available">{{ t('Disponible croissant, puis SKU') }}</option>
          <option value="price">{{ t('Prix croissant, puis SKU') }}</option>
          <option value="sku">SKU</option>
        </select></label>
        <label><span><input
          v-model="exclude"
          type="checkbox"
          :disabled="busy"
        /> {{ t('Exclure les ruptures') }}</span></label>
        <button :disabled="busy">
          {{ t('Appliquer les filtres') }}
        </button>
      </form>
      <p
        v-if="busy"
        role="status"
      >
        {{ t('Chargement…') }}
      </p>
      <p
        v-if="error"
        role="alert"
      >
        {{ t(error) }}
      </p>
      <template v-if="result">
        <p>{{ t('Observation du') }} {{ dateLabel(result.observed_at) }}</p>
        <p
          v-if="!result.items.length"
          role="status"
        >
          {{ t('Aucun produit ne correspond à ces filtres.') }}
        </p>
        <div class="b2b-grid">
          <article
            v-for="product in result.items"
            :key="product.product_id"
            class="b2b-card"
          >
            <h3>{{ product.name }}</h3>
            <p>{{ product.sku }} · {{ formatMoney(product.price_minor, product.currency) }}</p>
            <p>{{ t('Physique') }} {{ product.quantity }} {{ t('· Réservé') }} {{ product.reserved }} {{ t('· Disponible') }} {{ product.available }}</p>
          </article>
        </div>
        <CataloguePagination
          :total="result.total"
          :offset="result.offset"
          :limit="result.limit"
          :busy="busy"
          @page="load($event)"
        />
      </template>
      <button
        class="secondary"
        @click="clear"
      >
        {{ t('Fermer les résultats actualisés') }}
      </button>
    </template>
  </section>
</template>
<style scoped>
.current-results { margin-block:16px; border-top:1px solid var(--border); padding-top:16px; }
form { flex-wrap:wrap; align-items:end; margin-bottom:16px; }
form label { min-width:0; max-width:100%; }
input:not([type=checkbox]) { width:9rem; max-width:100%; }
h3, p { overflow-wrap:anywhere; }
</style>
