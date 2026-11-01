<script setup lang="ts">
import { t } from './preferences'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import CatalogueCombobox from './CatalogueCombobox.vue'
import CataloguePagination from './CataloguePagination.vue'
import { BffError, StaleResponse } from './client'
import { RouterLink } from 'vue-router'
import DetailsPanel from './DetailsPanel.vue'
import ProductEditor from './ProductEditor.vue'
import ProductVisual from './ProductVisual.vue'
import StockStats from './StockStats.vue'
import ComparisonPanel from './ComparisonPanel.vue'
import ExportPanel from './ExportPanel.vue'
import { useB2B } from './session'
import { array, object, text, productDecoder, productsDecoder, stockDecoder, type Product, type Stock } from './contracts'
import { formatMoney } from './money'
const session = useB2B()
const detailTrigger = ref<HTMLElement | null>(null)
const busy = ref(false)
const error = ref('')
const listStart = ref<HTMLElement>()
const products = ref<Product[]>([])
const stocks = ref<Record<string, Stock>>({})
const offset = ref(0)
const total = ref(0)
const sku = ref('')
const category = ref('')
const available = ref(false)
const selected = ref<Product | null>(null)
const editor = ref<'create' | 'edit' | 'remove' | null>(null)
const edited = ref<Product>()
const exportOpen = ref(false)
const compared = ref<string[]>([])
const comparisonOpen = ref(false)
function edit(product: Product, remove = false) { edited.value = product; editor.value = remove ? 'remove' : 'edit' }
async function saved() { editor.value = null; compared.value = []; await load() }
const limit = 12
let disposed = false
let epoch = 0
let controller: AbortController | undefined
const categories = ref<string[]>([])
const categoryError = ref('')
const skuOptions = ref<Product[]>([])
const suggestBusy = ref(false)
const suggestError = ref('')
let suggestionEpoch = 0
let suggestionController: AbortController | undefined
let suggestionTimer: ReturnType<typeof setTimeout> | undefined
const normalized = (value: string) => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase()
const categoryOptions = computed(() => [
  { value: '', label: t('Toutes les catégories') },
  ...categories.value.filter(c => normalized(c).includes(normalized(category.value))).map(c => ({ value: c, label: c.charAt(0).toLocaleUpperCase() + c.slice(1) })),
])
const suggestions = computed(() => skuOptions.value.map(p => ({ value: p.sku, label: p.sku, detail: p.name + ' · ' + p.category })))
function invalidate() {
  epoch++; controller?.abort(); busy.value = false
  offset.value = 0; total.value = 0; products.value = []; stocks.value = {}; selected.value = null; error.value = ''
}
watch([sku, category, available], invalidate, { flush: 'sync' })
watch(sku, value => {
  const ticket = ++suggestionEpoch
  clearTimeout(suggestionTimer); suggestionController?.abort(); skuOptions.value = []; suggestError.value = ''; suggestBusy.value = false
  const prefix = value.trim()
  if (prefix.length < 2) return
  suggestBusy.value = true
  suggestionTimer = setTimeout(async () => {
    const request = new AbortController(); suggestionController = request
    try {
      const result = await session.client.request('/b2b/api/products/suggestions?' + new URLSearchParams({ prefix, limit: '10' }), object({ items: array(productDecoder, 20) }), { signal: request.signal })
      if (ticket === suggestionEpoch) skuOptions.value = result.items
    } catch (e) { if (ticket === suggestionEpoch && !(e instanceof StaleResponse)) suggestError.value = t('Suggestions indisponibles. La saisie libre reste possible.') }
    finally { if (ticket === suggestionEpoch) suggestBusy.value = false }
  }, 250)
})
async function load(reset = false) {
  controller?.abort(); const request = new AbortController(); controller = request
  const ticket = ++epoch
  if (reset) offset.value = 0
  busy.value = true; error.value = ''; selected.value = null; products.value = []; stocks.value = {}; total.value = 0
  const exactSku = sku.value.trim()
  try {
    let items: Product[]; let count: number
    if (exactSku) {
      const product = await session.client.request('/b2b/api/products/by-sku?' + new URLSearchParams({ sku: exactSku }), productDecoder, { signal: request.signal })
      items = [product]; count = 1
    } else {
      const query = new URLSearchParams({ limit: String(limit), offset: String(offset.value), status: 'active' })
      // Suggestion matching is accent-insensitive; the submitted identity is canonical.
      const matches = categories.value.filter(c => normalized(c) === normalized(category.value.trim()))
      const selectedCategory = matches.length === 1 ? matches[0]! : category.value.trim()
      if (selectedCategory) query.set('category', selectedCategory)
      if (available.value) query.set('available', 'true')
      const result = await session.client.request('/b2b/api/products?' + query, productsDecoder, { signal: request.signal })
      items = result.items; count = result.total
    }
    if (ticket !== epoch) return
    const observed: Record<string, Stock> = {}
    if (session.has('stock:read')) {
      for (let i = 0; i < items.length; i += 3) {
        const group = await Promise.all(items.slice(i, i + 3).map(async p => {
          const stock = await session.client.request('/b2b/api/products/' + encodeURIComponent(p.product_id) + '/stock', stockDecoder, { signal: request.signal })
          return [p.product_id, stock] as const
        }))
        if (ticket !== epoch) return
        Object.assign(observed, Object.fromEntries(group))
      }
    }
    if (ticket === epoch) { products.value = items; total.value = count; stocks.value = observed }
  } catch (e) {
    if (ticket === epoch && !(e instanceof StaleResponse)) error.value = exactSku && e instanceof BffError && e.status === 404 ? t('Aucun produit trouvé pour ce SKU exact.') : e instanceof Error ? e.message : t('Lecture du catalogue impossible.')
  } finally { if (ticket === epoch) busy.value = false }
}
async function navigate(page: number) {
  if (busy.value || page < 1 || (page - 1) * limit > 10000) return
  offset.value = (page - 1) * limit; await load()
  await nextTick(); listStart.value?.scrollIntoView?.({ block: 'start' }); listStart.value?.focus({ preventScroll: true })
}
async function detail(product: Product, event: Event) {
  detailTrigger.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  const ticket = epoch
  busy.value = true
  try {
    const value = await session.client.request('/b2b/api/products/' + encodeURIComponent(product.product_id), productDecoder, { signal: controller?.signal })
    if (ticket === epoch) selected.value = value
  } catch (e) { if (ticket === epoch && !(e instanceof StaleResponse)) error.value = e instanceof Error ? e.message : t('Lecture du catalogue impossible.') }
  finally { if (ticket === epoch) busy.value = false }
}
function clearFilters() { sku.value = ''; category.value = ''; available.value = false; void load(true) }
onMounted(async () => {
  void load()
  try { const result = await session.client.request('/b2b/api/products/categories', object({ items: array(text, 1000) })); if (!disposed) categories.value = result.items }
  catch (e) { if (!(e instanceof StaleResponse)) categoryError.value = t('Catégories indisponibles. La saisie libre reste possible.') }
})
onUnmounted(() => { disposed = true; invalidate(); suggestionEpoch++; clearTimeout(suggestionTimer); suggestionController?.abort() })
// Deliberately no historical client or catalogue fixture in this view.
</script>
<template>
  <section>
    <div class="page-heading">
      <p class="eyebrow">
        {{ t("Votre organisation") }}
      </p>
      <h1>{{ t("Catalogue") }}</h1>
      <p>{{ t("Prix et disponibilités issus de votre catalogue. Une consultation ne réserve aucun stock.") }}</p>
      <div class="b2b-actions">
        <button
          v-if="session.has('import:execute')"
          @click="edited = undefined; editor = 'create'"
        >
          {{ t("Ajouter un produit") }}
        </button>
        <button
          class="secondary"
          @click="exportOpen = true"
        >
          {{ t("Exporter CSV") }}
        </button>
      </div>
    </div>
    <form
      class="b2b-card"
      @submit.prevent="load(true)"
    >
      <div class="b2b-catalogue-filters">
        <CatalogueCombobox
          id="catalogue-sku"
          v-model="sku"
          :label="t('SKU exact')"
          :options="suggestions"
          :loading="suggestBusy"
          :hint="t('Tapez au moins 2 caractères du début du SKU.')"
          @select="load(true)"
        />
        <CatalogueCombobox
          id="catalogue-category"
          v-model="category"
          :label="t('Catégorie')"
          :options="categoryOptions"
          :disabled="!!sku.trim()"
          @select="load(true)"
        />
        <label class="check-label availability-filter"><input
          v-model="available"
          type="checkbox"
          :disabled="!!sku.trim() || !session.has('stock:read')"
          @change="load(true)"
        /> {{ t("Afficher uniquement les produits disponibles") }}</label>
        <button :disabled="busy">
          {{ t("Rechercher") }}
        </button>
      </div>
      <button
        type="button"
        class="secondary"
        @click="clearFilters"
      >
        {{ t('Effacer les filtres') }}
      </button>
      <p
        v-if="categoryError || suggestError"
        role="status"
      >
        {{ categoryError || suggestError }}
      </p>
      <p class="hint">
        {{ t("Le SKU est une recherche exacte ; les autres filtres s’appliquent à la liste paginée.") }}
      </p>
    </form>
    <p
      v-if="error"
      role="alert"
    >
      {{ t(error) }}
    </p>
    <p
      v-if="busy"
      role="status"
    >
      {{ t("Chargement du catalogue…") }}
    </p>
    <p
      v-else-if="!error && !products.length"
      role="status"
    >
      {{ t("Aucun produit trouvé dans votre organisation.") }}
    </p>
    <div
      ref="listStart"
      tabindex="-1"
      :aria-label="t('Résultats du catalogue')"
    ></div>
    <CataloguePagination
      v-if="!sku.trim() && !error"
      :total="total"
      :offset="offset"
      :limit="limit"
      :busy="busy"
      @page="navigate"
    />
    <div class="b2b-grid">
      <article
        v-for="product in products"
        :key="product.product_id"
        class="b2b-card"
      >
        <ProductVisual :category="product.category" />
        <p class="eyebrow">
          {{ product.sku }}
        </p>
        <h2>{{ product.name }}</h2>
        <span class="category-tag">{{ product.category }}</span>
        <p class="product-summary">
          {{ product.description }}
        </p>
        <p class="b2b-price">
          {{ formatMoney(product.price_minor, product.currency) }}
        </p>
        <p
          v-if="stocks[product.product_id]?.available === 0"
          class="stock-unavailable"
        >
          {{ t('Rupture de stock') }}
        </p>
        <StockStats
          v-if="session.has('stock:read')"
          :stock="stocks[product.product_id]"
        />
        <div class="b2b-actions">
          <button
            class="secondary"
            :disabled="busy"
            @click="detail(product, $event)"
          >
            {{ t("Voir le produit") }}
          </button>
          <RouterLink
            v-if="session.has('order:create') && product.status === 'active' && (stocks[product.product_id]?.available ?? 0) > 0"
            :to="{ path: '/b2b-app/orders', query: { sku: product.sku } }"
          >
            {{ t("Préparer une commande") }}
          </RouterLink>
        </div>
        <div class="product-card-footer">
          <label class="check-label"><input
            v-model="compared"
            type="checkbox"
            :value="product.product_id"
            :disabled="compared.length >= 10 && !compared.includes(product.product_id)"
          /> {{ t("Comparer") }}</label>
          <div
            v-if="session.has('import:execute')"
            class="b2b-actions"
          >
            <button
              class="secondary"
              :disabled="busy"
              @click="edit(product)"
            >
              {{ t("Modifier") }}
            </button>
            <button
              class="secondary danger-link"
              :disabled="busy"
              @click="edit(product, true)"
            >
              {{ t("Retirer") }}
            </button>
          </div>
        </div>
      </article>
    </div>
    <div
      v-if="compared.length"
      class="comparison-bar"
      role="status"
    >
      <span>{{ compared.length }} {{ t("/ 10 produits sélectionnés") }}</span>
      <button
        :disabled="compared.length < 2"
        @click="comparisonOpen = true"
      >
        {{ t("Comparer les produits") }}
      </button>
      <button
        class="secondary"
        @click="compared = []"
      >
        {{ t("Effacer la sélection") }}
      </button>
    </div>
    <ProductEditor
      v-if="editor"
      :product="edited"
      :remove="editor === 'remove'"
      @close="editor = null"
      @saved="saved"
    />
    <ExportPanel
      v-if="exportOpen"
      @close="exportOpen = false"
    />
    <ComparisonPanel
      v-if="comparisonOpen"
      :ids="compared"
      @close="comparisonOpen = false"
    />
    <DetailsPanel
      v-if="selected"
      :title="selected.name"
      :return-focus="detailTrigger"
      :label="t('Détail produit')"
      @close="selected = null"
    >
      <p class="eyebrow">
        {{ t("Informations complémentaires ·") }} {{ selected.sku }}
      </p>
      <p>{{ t("Catégorie :") }} {{ selected.category }} · {{ selected.status === 'active' ? t("Au catalogue") : t("Retiré du catalogue") }}</p>
      <StockStats
        v-if="session.has('stock:read')"
        :stock="stocks[selected.product_id]"
      />
      <p>{{ selected.description || t("Description non renseignée.") }}</p>

      <ul>
        <li
          v-for="attribute in selected.attributes"
          :key="attribute"
        >
          {{ attribute }}
        </li>
      </ul>
      <p
        v-if="!selected.attributes.length"
        class="hint"
      >
        {{ t("Aucune caractéristique complémentaire renseignée.") }}
      </p>
    </DetailsPanel>
  </section>
</template>

<style scoped>
.stock-unavailable { color:var(--ink); background:var(--soft); border:1px solid var(--line); border-radius:6px; padding:6px 10px; width:fit-content; font-weight:700; }
</style>
