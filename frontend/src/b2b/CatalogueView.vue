<script setup lang="ts">
import { t } from './preferences'
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import DetailsPanel from './DetailsPanel.vue'
import ProductEditor from './ProductEditor.vue'
import ProductVisual from './ProductVisual.vue'
import ComparisonPanel from './ComparisonPanel.vue'
import ExportPanel from './ExportPanel.vue'
import { useB2B } from './session'
import { productDecoder, productsDecoder, stockDecoder, type Product, type Stock } from './contracts'
import { formatMoney } from './money'
import { useTask } from './tasks'
const session = useB2B()
const detailTrigger = ref<HTMLElement | null>(null)
const { busy, error, run } = useTask()
const products = ref<Product[]>([])
const stocks = ref<Record<string, Stock>>({})
const offset = ref(0)
const total = ref(0)
const more = ref(false)
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
async function load(reset = false) {
  await run(async () => {
    if (reset) offset.value = 0
    selected.value = null; products.value = []; stocks.value = {}
    if (sku.value.trim()) {
      const product = await session.client.request('/b2b/api/products/by-sku?' + new URLSearchParams({ sku: sku.value.trim() }), productDecoder)
      products.value = [product]; total.value = 1; more.value = false
    } else {
      const query = new URLSearchParams({ limit: String(limit), offset: String(offset.value), status: 'active' })
      if (category.value.trim()) query.set('category', category.value.trim())
      if (available.value) query.set('available', 'true')
      const result = await session.client.request('/b2b/api/products?' + query, productsDecoder)
      products.value = result.items; total.value = result.total; more.value = result.has_more
    }
    if (session.has('stock:read')) {
      // Three bounded requests at a time, below BFF admission. No unbounded fan-out.
      for (let i = 0; i < products.value.length; i += 3) {
        const group = await Promise.all(products.value.slice(i, i + 3).map(async p => {
          const stock = await session.client.request('/b2b/api/products/' + encodeURIComponent(p.product_id) + '/stock', stockDecoder)
          return [p.product_id, stock] as const
        }))
        stocks.value = { ...stocks.value, ...Object.fromEntries(group) }
      }
    }
  })
}
async function navigate(delta: number) { offset.value += delta * limit; await load() }
async function detail(product: Product, event: Event) {
  detailTrigger.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  await run(async () => { selected.value = await session.client.request('/b2b/api/products/' + encodeURIComponent(product.product_id), productDecoder) })
}
onMounted(() => { void load() })
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
      <div class="b2b-actions">
        <label>{{ t("SKU exact") }}
          <input
            v-model="sku"
            maxlength="128"
            :disabled="busy"
            placeholder="Ex. DRILL-001"
          />
        </label>
        <label>{{ t("Catégorie exacte") }}
          <input
            v-model="category"
            maxlength="128"
            :disabled="busy || !!sku.trim()"
          />
        </label>
        <label class="check-label availability-filter"><input
          v-model="available"
          type="checkbox"
          :disabled="busy || !!sku.trim()"
        /> {{ t("Afficher uniquement les produits disponibles") }}</label>
        <button :disabled="busy">
          {{ t("Rechercher") }}
        </button>
      </div>
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
      v-else-if="!products.length"
      role="status"
    >
      {{ t("Aucun produit trouvé dans votre organisation.") }}
    </p>
    <p v-else>
      {{ total }} {{ t("produit(s) · page") }} {{ Math.floor(offset / limit) + 1 }}
    </p>
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
        <p v-if="stocks[product.product_id]">
          {{ t("Disponible :") }} <strong>{{ stocks[product.product_id]?.available }}</strong>
          {{ t("· Réservé :") }} {{ stocks[product.product_id]?.reserved }}
          {{ t("· Physique :") }} {{ stocks[product.product_id]?.quantity }}
        </p>
        <div class="b2b-actions">
          <button
            class="secondary"
            :disabled="busy"
            @click="detail(product, $event)"
          >
            {{ t("Voir le produit") }}
          </button>
          <RouterLink
            v-if="session.has('order:create') && product.status === 'active'"
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
            :disabled="compared.length >= 4 && !compared.includes(product.product_id)"
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
      <span>{{ compared.length }} {{ t("/ 4 produits sélectionnés") }}</span>
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
    <nav
      v-if="!sku.trim()"
      class="b2b-actions"
      :aria-label="t('Pages du catalogue')"
    >
      <button
        class="secondary"
        :disabled="busy || offset === 0"
        @click="navigate(-1)"
      >
        {{ t("Précédent") }}
      </button>
      <button
        class="secondary"
        :disabled="busy || !more || offset + limit > 10000"
        @click="navigate(1)"
      >
        {{ t("Suivant") }}
      </button>
    </nav>
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
