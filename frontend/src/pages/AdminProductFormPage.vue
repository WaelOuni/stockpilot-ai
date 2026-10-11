<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { createAdminProduct, getAdminProduct, updateAdminProduct } from '../api/admin'
import { ApiError } from '../api/http'
import type { ProductResponse as Product, ProductCategory } from '../types/api'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import AdminLogout from '../components/AdminLogout.vue'
import { productCategoryLabels } from '../utils/products'

const route = useRoute()
const router = useRouter()
const editing = computed(() => route.params.sku !== undefined)
const routeSku = computed(() => typeof route.params.sku === 'string' ? route.params.sku : '')
const sku = ref('')
const name = ref('')
const category = ref<ProductCategory>('OTHER')
const price = ref<string | number>('')
const product = ref<Product | null>(null)
const loading = ref(false)
const saving = ref(false)
const blocked = ref(false)
const error = ref<Error | null>(null)
const feedback = ref('')
const loggingOut = ref(false)
let controller: AbortController | undefined
let mutationTimeout: ReturnType<typeof setTimeout> | undefined
let reconciliationSku = ''
function logoutPending(pending: boolean) {
  loggingOut.value = pending
  if (pending) { blocked.value = true; controller?.abort() }
}
function accept(value: Product) {
  product.value = value
  sku.value = value.sku
  name.value = value.product_name
  category.value = value.category
  price.value = String(value.price)
}
async function report(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) {
    await router.replace('/admin/login?reason=session')
  } else error.value = cause instanceof Error ? cause : new Error('Opération impossible.')
}
async function load() {
  if (saving.value || loading.value || loggingOut.value) return
  const target = editing.value ? routeSku.value : reconciliationSku
  if (!target) return
  controller?.abort()
  const request = new AbortController()
  controller = request
  loading.value = true
  feedback.value = ''
  error.value = null
  try {
    const value = await getAdminProduct(target, request.signal)
    if (request.signal.aborted) return
    accept(value)
    blocked.value = !editing.value
    if (!editing.value) feedback.value = 'Ce SKU est présent. Création bloquée ; consultez le produit avant de continuer.'
  } catch (cause) {
    if (!request.signal.aborted) {
      if (cause instanceof ApiError && cause.status === 404) {
        product.value = null
        blocked.value = editing.value
        if (!editing.value) feedback.value = 'Lecture fiable : SKU absent. Une nouvelle soumission manuelle est possible.'
        else await report(cause)
      } else { blocked.value = true; await report(cause) }
    }
  } finally { if (controller === request) loading.value = false }
}
async function submit() {
  if (saving.value || loading.value || blocked.value || loggingOut.value) return
  error.value = null
  feedback.value = ''
  const amount = Number(price.value)
  if (!sku.value.trim() || !name.value.trim() || !String(price.value).trim()
    || !Number.isFinite(amount) || amount < 0 || !Object.hasOwn(productCategoryLabels, category.value)) {
    error.value = new Error('SKU et nom non vides, catégorie valide et prix fini positif ou nul requis.')
    return
  }
  const request = new AbortController()
  controller?.abort()
  controller = request
  saving.value = true
  reconciliationSku = editing.value ? routeSku.value : sku.value
  const timeout = setTimeout(() => request.abort(), 20_000)
  mutationTimeout = timeout
  let reconcile = false
  try {
    const values = { name: name.value, category: category.value, price: amount }
    const result = editing.value
      ? await updateAdminProduct(routeSku.value, values, request.signal)
      : await createAdminProduct({ ...values, sku: sku.value }, request.signal)
    if (request.signal.aborted) throw new ApiError('Délai dépassé. Vérifiez le produit.', 0)
    accept(result)
    feedback.value = editing.value ? 'Métadonnées enregistrées.' : 'Produit créé avec un stock initial de 0.'
    blocked.value = !editing.value
  } catch (cause) {
    if (controller !== request) return
    blocked.value = true
    await report(request.signal.aborted ? new ApiError('Résultat incertain. Actualisation requise, sans renvoi automatique.', 0) : cause)
    reconcile = !(cause instanceof ApiError && cause.status === 401)
  } finally {
    clearTimeout(timeout)
    if (mutationTimeout === timeout) mutationTimeout = undefined
    if (controller === request) saving.value = false
  }
  if (reconcile && controller === request) {
    const originalError = error.value
    await load()
    if (!error.value) error.value = originalError
  }
}
watch(() => route.fullPath, () => {
  controller?.abort()
  clearTimeout(mutationTimeout)
  mutationTimeout = undefined
  controller = undefined
  loading.value = saving.value = false
  blocked.value = editing.value
  product.value = null
  error.value = null
  feedback.value = ''
  reconciliationSku = ''
  sku.value = routeSku.value
  name.value = price.value = ''
  category.value = 'OTHER'
  if (editing.value) void load()
}, { immediate: true })
onBeforeUnmount(() => {
  controller?.abort()
  controller = undefined
  clearTimeout(mutationTimeout)
})
</script>
<template>
  <header class="page-heading page-heading-actions">
    <div>
      <span class="eyebrow">Back-office · Métadonnées uniquement</span>
      <h1>{{ editing ? 'Modifier un produit' : 'Créer un produit' }}</h1>
    </div>
    <div class="header-actions">
      <button
        v-if="editing || blocked"
        class="secondary"
        :disabled="loading || saving || loggingOut"
        @click="load"
      >
        Actualiser le produit
      </button>
      <AdminLogout @pending="logoutPending" />
    </div>
  </header>
  <nav
    class="page-actions"
    aria-label="Actions produit"
  >
    <RouterLink to="/admin/products">
      Retour aux produits
    </RouterLink>
    <RouterLink
      v-if="editing"
      :to="`/admin/products/${encodeURIComponent(routeSku)}/stock`"
    >
      Ajuster le stock
    </RouterLink>
  </nav>
  <aside
    v-if="editing"
    class="info-notice"
    role="note"
    aria-labelledby="rename-title"
  >
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
    ><circle
      cx="12"
      cy="12"
      r="9"
    /><path d="M12 11v6 M12 7v1" /></svg>
    <div>
      <h2 id="rename-title">
        Attention au renommage
      </h2><p>
        Renommer ne modifie pas les commandes existantes ; leur restitution utilise l’identifiant interne.
        Une confirmation déjà préparée par ancien nom peut échouer après renommage.
      </p>
    </div>
  </aside>
  <p
    v-if="loading"
    role="status"
  >
    Chargement du produit…
  </p>
  <ApiErrorNotice
    v-if="error"
    id="form-error"
    :error="error"
  />
  <p
    v-if="feedback && !loading"
    :class="error ? 'info-notice' : 'success-notice'"
    role="status"
  >
    {{ feedback }}
  </p>
  <dl class="product-stock-summary">
    <div><dt>Stock (lecture seule)</dt><dd>{{ product ? product.stock : editing ? 'indisponible' : '0 à la création' }}</dd></div>
  </dl>
  <form
    :aria-describedby="error ? 'form-error' : undefined"
    @submit.prevent="submit"
  >
    <fieldset :disabled="saving || loading || blocked || loggingOut">
      <label for="product-sku">SKU (immuable après création)</label>
      <input
        id="product-sku"
        v-model="sku"
        :readonly="editing"
        :title="sku"
        :aria-describedby="editing ? 'product-sku-value' : undefined"
        required
      />
      <p
        v-if="editing"
        id="product-sku-value"
        class="identifier-value hint"
      >
        {{ sku }}
      </p>
      <label for="product-name">Nom</label>
      <input
        id="product-name"
        v-model="name"
        required
      />
      <label for="product-category">Catégorie</label>
      <select
        id="product-category"
        v-model="category"
        required
      >
        <option
          v-for="(label, value) in productCategoryLabels"
          :key="value"
          :value="value"
        >
          {{ label }}
        </option>
      </select>
      <label for="product-price">Prix</label>
      <input
        id="product-price"
        v-model="price"
        type="number"
        min="0"
        step="any"
        required
      />
      <button type="submit">
        {{ saving ? 'Enregistrement…' : editing ? 'Enregistrer' : 'Créer' }}
      </button>
    </fieldset>
  </form>
  <RouterLink
    v-if="!editing && product"
    :to="`/admin/products/${encodeURIComponent(product.sku)}/edit`"
  >
    Consulter / modifier {{ product.product_name }}
  </RouterLink>
</template>
