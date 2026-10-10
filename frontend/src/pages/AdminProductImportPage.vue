<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { executeAdminProductCsv, getAdminProductCsvBatch, previewAdminProductCsv } from '../api/admin'
import type { CsvImportResult, CsvPreview } from '../api/admin'
import { ApiError } from '../api/http'
import AdminLogout from '../components/AdminLogout.vue'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'

const router = useRouter()
const file = ref<File | null>(null)
const preview = ref<CsvPreview | null>(null)
const result = ref<CsvImportResult | null>(null)
const error = ref<Error | null>(null)
const loading = ref(false)
const confirmed = ref(false)
const pending = ref<{ operation_id: string; fingerprint: string } | null>(null)
const storageBlocked = ref(false)
const key = 'stockpilot_pending_product_import_v6'
try {
  const stored = sessionStorage.getItem(key)
  if (stored) {
    const parsed = JSON.parse(stored)
    if (!parsed || Object.keys(parsed).sort().join(',') !== 'fingerprint,operation_id'
      || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(parsed.operation_id)
      || !/^[0-9a-f]{64}$/.test(parsed.fingerprint)) throw new Error()
    pending.value = parsed
  }
} catch { storageBlocked.value = true; error.value = new Error('Stockage d’opération indisponible ou corrompu. Import bloqué.') }
const blocked = computed(() => Boolean(pending.value) || storageBlocked.value || Boolean(result.value))
const canExecute = computed(() => !loading.value && !blocked.value && Boolean(file.value)
  && Boolean(preview.value?.binding) && confirmed.value)
let controller: AbortController | undefined
let epoch = 0
let timer: ReturnType<typeof setTimeout> | undefined
function begin() {
  controller?.abort()
  clearTimeout(timer)
  const request = new AbortController()
  controller = request
  const current = ++epoch
  loading.value = true
  error.value = null
  timer = setTimeout(() => request.abort(), 20_000)
  return { request, current }
}
async function failed(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) await router.replace('/admin/login?reason=session')
  else error.value = cause instanceof Error && cause.name !== 'AbortError'
    ? cause : new Error('Réponse interrompue ou délai dépassé. Rapprochez l’opération avant toute action.')
}
function finish(current: number) {
  if (current === epoch) { loading.value = false; clearTimeout(timer) }
}
function select(event: Event) {
  if (blocked.value) return
  controller?.abort()
  clearTimeout(timer)
  epoch++
  loading.value = false
  file.value = null
  preview.value = null
  confirmed.value = false
  error.value = null
  const selected = (event.target as HTMLInputElement).files?.[0]
  if (!selected) return
  if (!selected.size || selected.size > 2 * 1024 * 1024) {
    error.value = new Error('Sélectionnez un CSV non vide de 2 MiB maximum.')
    return
  }
  file.value = selected
}
async function analyse() {
  if (!file.value || loading.value || blocked.value) return
  preview.value = null
  confirmed.value = false
  const { request, current } = begin()
  try {
    const value = await previewAdminProductCsv(file.value, request.signal)
    if (current === epoch && !request.signal.aborted) preview.value = value
  } catch (cause) { if (current === epoch) await failed(cause) }
  finally { finish(current) }
}
async function execute() {
  if (!canExecute.value || !file.value || !preview.value?.binding) return
  const operation = { operation_id: crypto.randomUUID(), fingerprint: preview.value.fingerprint }
  try { sessionStorage.setItem(key, JSON.stringify(operation)) }
  catch { storageBlocked.value = true; error.value = new Error('Stockage indisponible : aucun import envoyé.'); return }
  pending.value = operation
  const { request, current } = begin()
  try {
    const value = await executeAdminProductCsv(file.value, operation.operation_id, preview.value.binding, request.signal)
    if (current === epoch && !request.signal.aborted) accept(value)
  } catch (cause) {
    if (current === epoch) {
      // A session-generation change can surface as 401 after a commit; keep its operation.
      if (cause instanceof ApiError && [403, 409, 413, 422].includes(cause.status)) {
        try { sessionStorage.removeItem(key); pending.value = null; preview.value = null; confirmed.value = false }
        catch { storageBlocked.value = true }
      }
      await failed(cause)
    }
  } finally { finish(current) }
}
function accept(value: CsvImportResult) {
  if (!pending.value || value.fingerprint !== pending.value.fingerprint) throw new ApiError('Résultat incohérent.', 0)
  result.value = value
  sessionStorage.removeItem(key)
  pending.value = null
  file.value = null
  preview.value = null
  confirmed.value = false
}
async function reconcile() {
  if (!pending.value || loading.value) return
  const { request, current } = begin()
  try {
    const value = await getAdminProductCsvBatch(pending.value.operation_id, request.signal)
    if (current === epoch && !request.signal.aborted) accept(value)
  } catch (cause) { if (current === epoch) await failed(cause) }
  finally { finish(current) }
}
const example = 'data:text/csv;charset=utf-8,' + encodeURIComponent(
  'sku,name,category,price\nEXAMPLE-DRILL,Perceuse exemple,DRILL,99.5\nEXAMPLE-SAW,Scie exemple,SAW,49\n',
)
onBeforeUnmount(() => { epoch++; controller?.abort(); clearTimeout(timer) })
</script>
<template>
  <header class="page-heading">
    <span class="eyebrow">Back-office</span>
    <h1>Importer des produits CSV</h1>
  </header>
  <AdminLogout />
  <RouterLink to="/admin/products">
    Retour au catalogue administrateur
  </RouterLink>
  <p class="hint">
    CREATE_ONLY : nouveaux SKU uniquement, stock initial zéro. Aucun produit existant ne sera modifié.
    UTF-8 (BOM accepté), virgule ou point-virgule, colonnes sku, name, category, price exactement une fois.
    Catégories : DRILL, SAW, SANDER, SCREWDRIVER, OTHER. Maximum 2 MiB et 500 produits.
    L’aperçu ne réserve pas les SKU : un conflit concurrent refuse tout le lot.
  </p>
  <a
    :href="example"
    download="products-example.csv"
  >Télécharger un exemple CSV</a>
  <label>
    Fichier CSV
    <input
      type="file"
      accept=".csv,text/csv"
      :disabled="blocked || loading"
      @change="select"
    />
  </label>
  <button
    :disabled="!file || loading || blocked"
    @click="analyse"
  >
    Prévisualiser
  </button>
  <p
    v-if="loading"
    role="status"
  >
    Opération en cours…
  </p>
  <ApiErrorNotice
    v-if="error"
    :error="error"
  />
  <section v-if="preview">
    <h2>Aperçu</h2>
    <p>Analysés : {{ preview.analysed }} · Valides : {{ preview.valid }} · Invalides : {{ preview.invalid }} · SKU existants : {{ preview.existing_conflicts }}</p>
    <ul>
      <li
        v-for="(issue, index) in preview.issues"
        :key="index"
      >
        Ligne {{ issue.line }} : {{ issue.code }} — {{ issue.message }}
      </li>
    </ul>
    <p v-if="preview.issues_truncated">
      Liste d’erreurs limitée à 100 ; corrigez puis prévisualisez à nouveau.
    </p>
    <table>
      <caption>Produits valides à créer (stock zéro)</caption>
      <thead><tr><th>Ligne</th><th>SKU</th><th>Nom</th><th>Catégorie</th><th>Prix</th></tr></thead>
      <tbody>
        <tr
          v-for="(product, index) in preview.products"
          :key="product.sku"
        >
          <td>{{ preview.product_lines[index] }}</td><td>{{ product.sku }}</td><td>{{ product.name }}</td><td>{{ product.category }}</td><td>{{ product.price }}</td>
        </tr>
      </tbody>
    </table>
    <label>
      <input
        v-model="confirmed"
        type="checkbox"
        :disabled="!preview.binding || loading || blocked"
      />
      Je confirme la création de tous ces nouveaux produits sans mise à jour.
    </label>
    <button
      :disabled="!canExecute"
      @click="execute"
    >
      Confirmer l’import
    </button>
  </section>
  <section v-if="pending">
    <h2>Résultat incertain</h2>
    <p>Opération {{ pending.operation_id }}. Aucun nouvel import autorisé ; un résultat absent ne prouve pas un échec. Ne renvoyez pas le lot.</p>
    <button
      :disabled="loading"
      @click="reconcile"
    >
      Vérifier le résultat du lot
    </button>
  </section>
  <section
    v-if="result"
    role="status"
  >
    <h2>Import confirmé par le serveur</h2>
    <p>{{ result.created }} produits créés, stock zéro.</p>
    <ul>
      <li
        v-for="sku in result.skus"
        :key="sku"
      >
        {{ sku }}
      </li>
    </ul>
    <RouterLink to="/admin/products">
      Consulter le catalogue
    </RouterLink>
  </section>
</template>
