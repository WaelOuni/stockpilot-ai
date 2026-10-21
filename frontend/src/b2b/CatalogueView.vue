<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useB2B } from './session'
import { productDecoder, productsDecoder, stockDecoder, type Product, type Stock } from './contracts'
import { formatMoney } from './money'
import { useTask } from './tasks'
const session = useB2B()
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
async function detail(product: Product) {
  await run(async () => { selected.value = await session.client.request('/b2b/api/products/' + encodeURIComponent(product.product_id), productDecoder) })
}
onMounted(() => { void load() })
// Deliberately no historical client or catalogue fixture in this view.
</script>
<template>
  <section>
    <div class="page-heading">
      <p class="eyebrow">
        Votre organisation
      </p>
      <h1>Catalogue</h1>
      <p>Prix et disponibilités issus de votre catalogue. Une consultation ne réserve aucun stock.</p>
    </div>
    <form
      class="b2b-card"
      @submit.prevent="load(true)"
    >
      <div class="b2b-actions">
        <label>SKU exact
          <input
            v-model="sku"
            maxlength="128"
            :disabled="busy"
            placeholder="Ex. DRILL-001"
          />
        </label>
        <label>Catégorie exacte
          <input
            v-model="category"
            maxlength="128"
            :disabled="busy || !!sku.trim()"
          />
        </label>
        <label><span>Disponibilité</span>
          <span><input
            v-model="available"
            type="checkbox"
            :disabled="busy || !!sku.trim()"
          /> En stock uniquement</span>
        </label>
        <button :disabled="busy">
          Rechercher
        </button>
      </div>
      <p class="hint">
        Le SKU est une recherche exacte ; les autres filtres s’appliquent à la liste paginée.
      </p>
    </form>
    <p
      v-if="error"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-if="busy"
      role="status"
    >
      Chargement du catalogue…
    </p>
    <p
      v-else-if="!products.length"
      role="status"
    >
      Aucun produit trouvé dans votre organisation.
    </p>
    <p v-else>
      {{ total }} produit(s) · page {{ Math.floor(offset / limit) + 1 }}
    </p>
    <div class="b2b-grid">
      <article
        v-for="product in products"
        :key="product.product_id"
        class="b2b-card"
      >
        <p class="eyebrow">
          {{ product.sku }}
        </p>
        <h2>{{ product.name }}</h2>
        <p class="b2b-price">
          {{ formatMoney(product.price_minor, product.currency) }}
        </p>
        <p v-if="stocks[product.product_id]">
          Disponible : <strong>{{ stocks[product.product_id]?.available }}</strong>
          · Réservé : {{ stocks[product.product_id]?.reserved }}
          · Physique : {{ stocks[product.product_id]?.quantity }}
        </p>
        <div class="b2b-actions">
          <button
            class="secondary"
            :disabled="busy"
            @click="detail(product)"
          >
            Voir le produit
          </button>
          <RouterLink
            v-if="session.has('order:create') && product.status === 'active'"
            :to="{ path: '/b2b-app/orders', query: { sku: product.sku } }"
          >
            Préparer une commande
          </RouterLink>
        </div>
      </article>
    </div>
    <nav
      v-if="!sku.trim()"
      class="b2b-actions"
      aria-label="Pages du catalogue"
    >
      <button
        class="secondary"
        :disabled="busy || offset === 0"
        @click="navigate(-1)"
      >
        Précédent
      </button>
      <button
        class="secondary"
        :disabled="busy || !more || offset + limit > 10000"
        @click="navigate(1)"
      >
        Suivant
      </button>
    </nav>
    <section
      v-if="selected"
      class="b2b-card"
      aria-label="Détail produit"
    >
      <h2>{{ selected.name }}</h2>
      <p>SKU {{ selected.sku }} · {{ selected.category }} · {{ selected.status }}</p>
      <p>{{ selected.description || 'Description non renseignée.' }}</p>
      <p>{{ formatMoney(selected.price_minor, selected.currency) }}</p>
      <ul>
        <li
          v-for="attribute in selected.attributes"
          :key="attribute"
        >
          {{ attribute }}
        </li>
      </ul>
      <button
        class="secondary"
        @click="selected = null"
      >
        Fermer le détail
      </button>
    </section>
  </section>
</template>
