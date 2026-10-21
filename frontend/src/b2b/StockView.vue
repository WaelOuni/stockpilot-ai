<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useB2B } from './session'
import { movementDecoder, page, previewDecoder, productDecoder, resultDecoder, stockDecoder, type Movement, type OperationResult, type Preview, type Product, type Stock } from './contracts'
import { useTask } from './tasks'
import { mutate, physicalQuantity } from './workflows'
const session = useB2B()
const { busy, error, run } = useTask()
const sku = ref('')
const product = ref<Product | null>(null)
const stock = ref<Stock | null>(null)
const quantity = ref('')
const reason = ref('')
const movements = ref<Movement[]>([])
const imports = ref<Preview[]>([])
const movementOffset = ref(0)
const importOffset = ref(0)
const limit = 10
const mode = ref<'create_only' | 'metadata_only' | 'stock_only'>('stock_only')
const file = ref<File | null>(null)
const preview = ref<Preview | null>(null)
const confirmed = ref(false)
const previewFilename = ref('')
let selectionVersion = 0
const result = ref<OperationResult | null>(null)
const notice = ref('')
const pending = computed(() => session.pending.value.length > 0)
const schemas = {
  stock_only: 'sku,quantity',
  metadata_only: 'sku,name,description,category,price_minor,currency',
  create_only: 'sku,name,description,category,price_minor,currency,quantity',
}
watch(mode, () => { selectionVersion++; preview.value = null; confirmed.value = false }, { flush: 'sync' })
function selectFile(event: Event) {
  selectionVersion++
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  preview.value = null; confirmed.value = false; notice.value = ''
}
async function loadStock() {
  product.value = null; stock.value = null
  const item = await session.client.request('/b2b/api/products/by-sku?' + new URLSearchParams({ sku: sku.value.trim() }), productDecoder)
  const inventory = await session.client.request('/b2b/api/products/' + encodeURIComponent(item.product_id) + '/stock', stockDecoder)
  product.value = item; stock.value = inventory; quantity.value = String(inventory.quantity)
}
async function loadMovements() {
  movements.value = (await session.client.request('/b2b/api/stock/movements?' + new URLSearchParams({ limit: String(limit), offset: String(movementOffset.value) }), page(movementDecoder))).items
}
async function loadImports() {
  if (session.has('import:execute')) imports.value = (await session.client.request('/b2b/api/imports?' + new URLSearchParams({ limit: String(limit), offset: String(importOffset.value) }), page(previewDecoder))).items
}
function describe(value: OperationResult) {
  result.value = value
  notice.value = value.status === 'succeeded' ? 'Opération enregistrée et confirmée par le serveur.'
    : value.status === 'pending' ? 'Traitement en attente. Vérifiez le résultat avec la même référence.'
      : 'Opération refusée, sans application du lot : ' + value.code
}
async function adjust() {
  await run(async () => {
    if (!product.value || !stock.value) return
    const physical = physicalQuantity(quantity.value)
    if (physical < stock.value.reserved) throw new Error('Le stock physique ne peut pas être inférieur au stock réservé.')
    if (!reason.value.trim() || Array.from(reason.value).some(c => c.charCodeAt(0) < 32)) throw new Error('Saisissez un motif métier sur une seule ligne.')
    const value = await mutate(session, 'stock', 'adjust', '/b2b/api/products/' + encodeURIComponent(product.value.product_id) + '/adjustments',
      id => ({ operation_id: id, quantity: physical, expected_revision: stock.value!.revision, reason: reason.value.trim() }), resultDecoder)
    describe(value)
    await loadStock(); await loadMovements()
  })
}
async function previewFile() {
  await run(async () => {
    preview.value = null; confirmed.value = false; result.value = null
    const version = selectionVersion
    const selectedMode = mode.value
    const selected = file.value
    if (!selected || selected.size > 256 * 1024) throw new Error('Sélectionnez un CSV UTF-8 de 256 Kio maximum (100 lignes de données).')
    let content: string
    try { content = new TextDecoder('utf-8', { fatal: true }).decode(await selected.arrayBuffer()) }
    catch { throw new Error('Le fichier doit être un CSV encodé en UTF-8.') }
    if (version !== selectionVersion) return
    const received = await session.client.request('/b2b/api/imports/preview?' + new URLSearchParams({ mode: selectedMode }), previewDecoder,
      { method: 'POST', csv: content })
    if (version !== selectionVersion) return
    preview.value = received; previewFilename.value = selected.name
    notice.value = 'Aperçu enregistré ; aucun stock ni produit modifié.'
    await loadImports()
  })
}
async function apply() {
  const draft = preview.value
  if (!draft || draft.status !== 'preview' || !confirmed.value) return
  await run(async () => {
    if (Date.parse(draft.expires_at) <= Date.now()) throw new Error('Aperçu expiré. Préparez un nouvel aperçu avant toute application.')
    const value = await mutate(session, 'stock', 'import', '/b2b/api/imports/apply',
      id => ({ operation_id: id, import_id: draft.import_id, preview_digest: draft.preview_digest, confirmed: true }), resultDecoder)
    describe(value); confirmed.value = false
    // A completed preview is not an invitation to generate another apply operation.
    if (value.status !== 'pending') preview.value = null
    await loadImports(); await loadMovements()
    if (product.value) await loadStock()
  })
}
async function movePage(delta: number) { movementOffset.value += delta * limit; await run(loadMovements) }
async function importPage(delta: number) { importOffset.value += delta * limit; await run(loadImports) }
onMounted(() => { void run(async () => { await loadMovements(); await loadImports() }) })
</script>
<template>
  <section>
    <div class="page-heading">
      <p class="eyebrow">
        Inventaire de votre organisation
      </p>
      <h1>Stock et administration</h1>
      <p>Physique − réservé = disponible. Les réservations restent gérées par les commandes.</p>
    </div>
    <p
      v-if="error"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-if="notice"
      class="b2b-notice"
      role="status"
    >
      {{ notice }}<span v-if="result"> Référence {{ result.operation_id }}.</span>
    </p>
    <section class="b2b-card">
      <h2>Consulter un stock</h2>
      <form
        class="b2b-actions"
        @submit.prevent="run(loadStock)"
      >
        <label>SKU du stock
          <input
            v-model="sku"
            maxlength="128"
            required
          />
        </label>
        <button :disabled="busy">
          Consulter le stock
        </button>
      </form>
      <div v-if="stock && product">
        <h3>{{ product.name }} · {{ product.sku }}</h3>
        <p>Physique : <strong>{{ stock.quantity }}</strong> · Réservé : <strong>{{ stock.reserved }}</strong> · Disponible : <strong>{{ stock.available }}</strong> · Révision {{ stock.revision }}</p>
        <form
          v-if="session.has('stock:adjust')"
          @submit.prevent="adjust"
        >
          <div class="b2b-actions">
            <label>Nouveau stock physique
              <input
                v-model="quantity"
                inputmode="numeric"
                pattern="[0-9]+"
                required
              />
            </label>
            <label>Motif de l’ajustement
              <input
                v-model="reason"
                maxlength="500"
                required
              />
            </label>
            <button :disabled="busy || pending">
              Enregistrer l’ajustement
            </button>
          </div>
          <p class="hint">
            Le serveur revalide la révision et les réservations au moment de l’écriture.
          </p>
        </form>
        <p v-else>
          Votre rôle autorise la consultation uniquement.
        </p>
      </div>
    </section>
    <section class="b2b-card">
      <div class="b2b-actions">
        <h2>Mouvements physiques</h2>
        <button
          class="secondary"
          :disabled="busy"
          @click="run(loadMovements)"
        >
          Actualiser les mouvements
        </button>
      </div>
      <p class="hint">
        Journal des ajustements et imports. Il ne constitue pas l’historique exhaustif des réservations ORDER.
      </p>
      <p v-if="!movements.length && !busy">
        Aucun mouvement physique à afficher.
      </p>
      <div
        v-if="movements.length"
        class="b2b-table"
      >
        <table>
          <caption class="sr-only">
            Mouvements de l’organisation
          </caption>
          <thead><tr><th>SKU</th><th>Physique</th><th>Réservé</th><th>Acteur / motif</th><th>Date</th></tr></thead>
          <tbody>
            <tr
              v-for="movement in movements"
              :key="movement.movement_id"
            >
              <td>{{ movement.sku }} · {{ movement.kind }}</td>
              <td>{{ movement.quantity_before }} → {{ movement.quantity_after }} ({{ movement.delta }})</td>
              <td>{{ movement.reserved_before }} → {{ movement.reserved_after }}</td>
              <td>{{ movement.actor_user_id }} · {{ movement.reason }}</td>
              <td>{{ new Date(movement.occurred_at).toLocaleString('fr-FR') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <nav
        class="b2b-actions"
        aria-label="Pages des mouvements"
      >
        <button
          class="secondary"
          :disabled="busy || movementOffset === 0"
          @click="movePage(-1)"
        >
          Précédent
        </button>
        <button
          class="secondary"
          :disabled="busy || movements.length < limit || movementOffset + limit > 10000"
          @click="movePage(1)"
        >
          Suivant
        </button>
      </nav>
    </section>
    <template v-if="session.has('import:execute')">
      <section class="b2b-card">
        <h2>Importer un CSV</h2>
        <p>Aperçu sans mutation, puis confirmation explicite d’un lot entièrement atomique.</p>
        <form @submit.prevent="previewFile">
          <div class="b2b-actions">
            <label>Type d’import
              <select
                v-model="mode"
                :disabled="busy"
              >
                <option value="stock_only">Stock physique uniquement</option>
                <option value="metadata_only">Descriptions et prix uniquement</option>
                <option value="create_only">Nouveaux produits uniquement</option>
              </select>
            </label>
            <label>Fichier CSV UTF-8
              <input
                type="file"
                accept=".csv,text/csv"
                :disabled="busy"
                required
                @change="selectFile"
              />
            </label>
            <button :disabled="busy || !file">
              Prévisualiser le CSV
            </button>
          </div>
          <p>256 Kio et 100 lignes maximum. En-têtes exacts : <code>{{ schemas[mode] }}</code></p>
          <p class="hint">
            Prix en unités mineures entières avec devise. Séparateur virgule ou point-virgule. Aucun champ ne modifie le stock réservé.
          </p>
        </form>
        <div
          v-if="preview"
          aria-label="Aperçu CSV"
        >
          <h3>{{ preview.status === 'preview' ? 'Aperçu prêt' : 'Aperçu invalide' }} · {{ preview.rows }} ligne(s)</h3>
          <p>Fichier {{ previewFilename }} · Mode {{ preview.mode }} · Référence {{ preview.import_id }}</p>
          <p>Empreinte {{ preview.file_fingerprint }}</p>
          <p>Valable jusqu’au {{ new Date(preview.expires_at).toLocaleString('fr-FR') }}</p>
          <ul>
            <li
              v-for="(issue, index) in preview.issues"
              :key="index"
            >
              Ligne {{ issue.line }} · {{ issue.code }} : {{ issue.message }}
            </li>
          </ul>
          <template v-if="preview.status === 'preview'">
            <label><span><input
              v-model="confirmed"
              type="checkbox"
            /> Je confirme l’application de cet aperçu à mon organisation.</span></label>
            <button
              :disabled="busy || pending || !confirmed"
              @click="apply"
            >
              Appliquer l’import
            </button>
          </template>
        </div>
      </section>
      <section class="b2b-card">
        <div class="b2b-actions">
          <h2>Historique des imports</h2>
          <button
            class="secondary"
            :disabled="busy"
            @click="run(loadImports)"
          >
            Actualiser les imports
          </button>
        </div>
        <p v-if="!imports.length && !busy">
          Aucun import à afficher.
        </p>
        <ul>
          <li
            v-for="entry in imports"
            :key="entry.import_id"
          >
            {{ entry.import_id }} · {{ entry.mode }} · {{ entry.status }} · {{ entry.rows }} ligne(s)
          </li>
        </ul>
        <p class="hint">
          Le statut d’un aperçu n’est pas une preuve d’application. Utilisez le résultat de l’opération pour confirmer un commit.
        </p>
        <nav
          class="b2b-actions"
          aria-label="Pages des imports"
        >
          <button
            class="secondary"
            :disabled="busy || importOffset === 0"
            @click="importPage(-1)"
          >
            Précédent
          </button>
          <button
            class="secondary"
            :disabled="busy || imports.length < limit || importOffset + limit > 10000"
            @click="importPage(1)"
          >
            Suivant
          </button>
        </nav>
      </section>
    </template>
  </section>
</template>
