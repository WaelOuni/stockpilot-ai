<script setup lang="ts">
import { t, dateLabel } from './preferences'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { BffError } from './client'
import DetailsPanel from './DetailsPanel.vue'
import { useB2B } from './session'
import { buyerDecoder, orderDecoder, productDecoder, stockDecoder, page, type Order } from './contracts'
import { formatMoney } from './money'
import { useTask } from './tasks'
import { mutate, positiveQuantity } from './workflows'
const session = useB2B()
const detailTrigger = ref<HTMLElement | null>(null)
const route = useRoute()
const { busy, error, run } = useTask()
const orders = ref<Order[]>([])
const buyers = ref<ReturnType<typeof buyerDecoder>[]>([])
const buyer = ref('')
const lines = ref([{ sku: typeof route.query.sku === 'string' ? route.query.sku : '', quantity: '1' }])
try {
  const saved = JSON.parse(session.continuity.draft('order') || 'null')
  if (saved && typeof saved.buyer === 'string' && Array.isArray(saved.lines) && saved.lines.length <= 20 && saved.lines.every((line: { sku: unknown; quantity: unknown }) => typeof line.sku === 'string' && typeof line.quantity === 'string')) {
    buyer.value = saved.buyer
    if (!route.query.sku) lines.value = saved.lines
  }
} catch { /* No usable in-memory draft. */ }
watch([buyer, lines], () => session.continuity.saveDraft('order', JSON.stringify({ buyer: buyer.value, lines: lines.value })), { deep: true, flush: 'sync' })
const quantityErrors = ref<Record<string, { requested: number; available: number | null }>>({})
function quantityError(sku: string, quantity: string) {
  const issue = quantityErrors.value[sku.trim()]
  if (!issue || issue.requested !== Number(quantity)) return ''
  return issue.available === null || !session.has('stock:read') ? t('Stock insuffisant pour la quantité demandée.')
    : t('Stock insuffisant :') + ' ' + issue.available + ' ' + t('unités disponibles,') + ' ' + issue.requested + ' ' + t('demandées.')
}
function stockRefusal(error: unknown): never {
  if (!(error instanceof BffError) || !error.stock) throw error
  quantityErrors.value[error.stock.sku] = error.stock
  throw new Error('Stock insuffisant pour la quantité demandée.')
}
const prepared = ref<Order | null>(null)
const chosen = ref<Order | null>(null)
const confirmation = ref(false)
const cancelChoice = ref<Order | null>(null)
const creationOpen = ref(typeof route.query.sku === 'string')
const confirmationOpen = ref(false)
const offset = ref(0)
const limit = 10
const pending = computed(() => session.pending.value.length > 0)
const notice = ref('')
const prepareReason = computed(() => session.phase.value !== 'active' ? 'Reconnectez-vous avant de préparer une commande.'
  : busy.value ? 'Chargement ou vérification en cours…'
  : pending.value ? 'Vérifiez d’abord les opérations en attente.'
  : !buyer.value ? 'Choisissez un acheteur autorisé.' : '')
const organizationScope = computed(() => session.has('order:read_organization'))
function label(order: Order) { return { prepared: 'Préparée — aucune réservation', reserved: 'Réservée', cancelled: 'Annulée' }[order.status] }
async function refresh() {
  const result = await session.client.request('/b2b/api/orders?' + new URLSearchParams({ limit: String(limit), offset: String(offset.value) }), page(orderDecoder))
  orders.value = result.items
}
async function prepare() {
  await run(async () => {
    confirmation.value = false; prepared.value = null; notice.value = ''
    if (!buyers.value.some(b => b.customer_id === buyer.value)) throw new Error('Choisissez un acheteur autorisé.')
    const requested = lines.value.map(l => ({ sku: l.sku.trim(), quantity: positiveQuantity(l.quantity) }))
    if (requested.some(l => !l.sku || l.sku.length > 128) || new Set(requested.map(l => l.sku)).size !== requested.length) throw new Error('Les SKU doivent être renseignés et distincts.')
    quantityErrors.value = {}
    if (session.has('stock:read')) {
      for (const line of requested) {
        const product = await session.client.request('/b2b/api/products/by-sku?sku=' + encodeURIComponent(line.sku), productDecoder)
        const stock = await session.client.request('/b2b/api/products/' + encodeURIComponent(product.product_id) + '/stock', stockDecoder)
        if (line.quantity > stock.available) quantityErrors.value[line.sku] = { requested: line.quantity, available: stock.available }
      }
      if (Object.keys(quantityErrors.value).length) return
    }
    prepared.value = await mutate(session, 'orders', 'prepare', '/b2b/api/orders/prepare',
      id => ({ operation_id: id, customer_id: buyer.value, lines: requested }), orderDecoder).catch(stockRefusal)
    notice.value = 'Préparation enregistrée. Aucun stock réservé.'
    creationOpen.value = false; confirmationOpen.value = true
    await refresh()
  })
}
async function confirm() {
  const draft = prepared.value
  if (!draft || !confirmation.value || draft.status !== 'prepared') return
  await run(async () => {
    const order = await mutate(session, 'orders', 'confirm', '/b2b/api/orders/' + encodeURIComponent(draft.order_id) + '/confirm',
      id => ({ operation_id: id, preparation_digest: draft.preparation_digest, confirmed: true }), orderDecoder).catch(stockRefusal)
    prepared.value = order; confirmation.value = false
    confirmationOpen.value = false
    notice.value = 'État serveur : ' + label(order); await refresh()
  })
}
async function cancel() {
  const selected = cancelChoice.value
  if (!selected) return
  await run(async () => {
    const result = await mutate(session, 'orders', 'cancel', '/b2b/api/orders/' + encodeURIComponent(selected.order_id) + '/cancel',
      id => ({ operation_id: id }), orderDecoder)
    notice.value = 'État serveur : ' + label(result)
    if (chosen.value?.order_id === result.order_id) chosen.value = result
    cancelChoice.value = null
    if (prepared.value?.order_id === result.order_id) prepared.value = result
    await refresh()
  })
}
async function details(order: Order, event: Event) {
  detailTrigger.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  await run(async () => { chosen.value = await session.client.request('/b2b/api/orders/' + encodeURIComponent(order.order_id), orderDecoder) })
}
async function next(delta: number) { offset.value += delta * limit; await run(refresh) }
function canCancel(order: Order) {
  return order.status === 'reserved' && (session.has('order:cancel_organization') || (session.has('order:cancel_own') && order.created_by === session.current.value?.user_id))
}
onMounted(() => { void run(async () => {
  if (session.has('order:create')) {
    buyers.value = (await session.client.request('/b2b/api/buyers?limit=100&offset=0', page(buyerDecoder))).items
    if (buyers.value.length === 1) buyer.value = buyers.value[0]!.customer_id
  }
  await refresh()
}) })
</script>
<template>
  <section>
    <div class="page-heading">
      <p class="eyebrow">
        {{ t("Suivi et réservation") }}
      </p>
      <h1>{{ t("Commandes") }}</h1>
      <p>{{ organizationScope ? t("Commandes de votre organisation") : t("Mes commandes : créées par mon utilisateur") }}{{ t(". L’acheteur partagé ne donne aucun droit sur les commandes d’un autre utilisateur.") }}</p>
      <div class="b2b-actions">
        <button
          v-if="session.has('order:create')"
          @click="creationOpen = true"
        >
          {{ t("Nouvelle commande") }}
        </button>
        <button
          v-if="prepared?.status === 'prepared' && !confirmationOpen"
          class="secondary"
          @click="confirmationOpen = true"
        >
          {{ t("Revoir la préparation") }}
        </button>
      </div>
    </div>
    <p
      v-if="error"
      role="alert"
    >
      {{ t(error) }}
    </p>
    <p
      v-if="notice"
      role="status"
      class="b2b-notice"
    >
      {{ t(notice) }}
    </p>
    <DetailsPanel
      v-if="session.has('order:create') && creationOpen"
      :title="t('Préparer une commande')"
      :label="t('Nouvelle commande')"
      :busy="busy"
      @close="creationOpen = false"
    >
      <p>{{ t("Aucun stock n’est réservé avant votre confirmation.") }}</p>
      <p
        v-if="error"
        role="alert"
      >
        {{ t(error) }}
      </p>
      <p
        id="prepare-reason"
        role="status"
      >
        {{ t(prepareReason) }}
      </p>
      <form @submit.prevent="prepare">
        <label>{{ t("Acheteur autorisé") }}
          <select
            v-model="buyer"
            required
          >
            <option value="">{{ t("Choisir un acheteur") }}</option>
            <option
              v-for="association in buyers"
              :key="association.association_id"
              :value="association.customer_id"
            >{{ association.customer_id }}</option>
          </select>
        </label>
        <p
          v-if="!buyers.length && !busy"
          class="hint"
        >
          {{ t("Aucun acheteur actif disponible. Contactez votre administrateur.") }}
        </p>
        <div
          v-for="(line, index) in lines"
          :key="index"
          class="b2b-actions"
        >
          <label>SKU {{ index + 1 }}
            <input
              v-model="line.sku"
              required
              maxlength="128"
            />
          </label>
          <label>{{ t("Quantité") }} {{ index + 1 }}
            <input
              v-model="line.quantity"
              :aria-invalid="!!quantityError(line.sku, line.quantity)"
              :aria-describedby="quantityError(line.sku, line.quantity) ? 'quantity-error-' + index : undefined"
              inputmode="numeric"
              pattern="[1-9][0-9]*"
              required
            />
          </label>
          <p
            v-if="quantityError(line.sku, line.quantity)"
            :id="'quantity-error-' + index"
            role="alert"
          >
            {{ quantityError(line.sku, line.quantity) }}
          </p>
          <button
            v-if="lines.length > 1"
            type="button"
            class="secondary"
            @click="lines.splice(index, 1)"
          >
            {{ t("Retirer la ligne") }} {{ index + 1 }}
          </button>
        </div>
        <div class="b2b-actions">
          <button
            type="button"
            class="secondary"
            :disabled="lines.length >= 20"
            @click="lines.push({ sku: '', quantity: '1' })"
          >
            {{ t("Ajouter une ligne") }}
          </button>
          <button
            :disabled="!!prepareReason"
            aria-describedby="prepare-reason"
          >
            {{ t("Préparer la commande") }}
          </button>
        </div>
      </form>
    </DetailsPanel>
    <DetailsPanel
      v-if="prepared && confirmationOpen"
      :title="t('Vérifier et confirmer la commande')"
      :label="t('Récapitulatif de préparation')"
      :busy="busy"
      @close="confirmationOpen = false; confirmation = false"
    >
      <p class="hint">
        {{ t("Référence") }} {{ prepared.order_id }}
      </p>
      <p
        v-if="error"
        role="alert"
      >
        {{ t(error) }}
      </p>
      <p class="b2b-status">
        {{ label(prepared) }}
      </p>
      <p>{{ t("Acheteur") }} {{ prepared.customer_id }} {{ t("· Valable jusqu’au") }} {{ dateLabel(prepared.expires_at) }}</p>
      <ul>
        <li
          v-for="line in prepared.lines"
          :key="line.product_id"
        >
          {{ line.sku }} {{ t("· quantité") }} {{ line.quantity }} {{ t("· prix unitaire") }} {{ formatMoney(line.price_minor, line.currency) }}
        </li>
      </ul>
      <template v-if="prepared.status === 'prepared'">
        <label>
          <span><input
            v-model="confirmation"
            type="checkbox"
          /> {{ t("Je confirme explicitement les produits, les quantités et les prix ci-dessus.") }}</span>
        </label>
        <button
          :disabled="busy || pending || !confirmation"
          @click="confirm"
        >
          {{ t("Confirmer et réserver") }}
        </button>
      </template>
    </DetailsPanel>
    <section class="b2b-card">
      <div class="b2b-actions">
        <h2>{{ organizationScope ? t("Historique de l’organisation") : t("Mon historique") }}</h2>
        <button
          class="secondary"
          :disabled="busy"
          @click="run(refresh)"
        >
          {{ t("Actualiser les commandes") }}
        </button>
      </div>
      <p
        v-if="busy"
        role="status"
      >
        {{ t("Chargement ou traitement en cours…") }}
      </p>
      <p v-else-if="!orders.length">
        {{ t("Aucune commande à afficher.") }}
      </p>
      <div
        v-if="orders.length"
        class="b2b-table"
      >
        <table>
          <caption class="sr-only">
            {{ t("Commandes autorisées") }}
          </caption>
          <thead><tr><th>{{ t("Commande") }}</th><th>{{ t("Créée par") }}</th><th>{{ t("État") }}</th><th>{{ t("Actions") }}</th></tr></thead>
          <tbody>
            <tr
              v-for="order in orders"
              :key="order.order_id"
            >
              <td>{{ order.order_id }}</td><td>{{ order.created_by }}</td><td>{{ label(order) }}</td>
              <td>
                <button
                  class="secondary"
                  :disabled="busy"
                  @click="details(order, $event)"
                >
                  {{ t("Voir la commande") }}
                </button> <button
                  v-if="canCancel(order)"
                  class="secondary"
                  :disabled="busy || pending"
                  @click="cancelChoice = order"
                >
                  {{ t("Annuler la commande") }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <nav
        class="b2b-actions"
        :aria-label="t('Pages des commandes')"
      >
        <button
          class="secondary"
          :disabled="busy || offset === 0"
          @click="next(-1)"
        >
          {{ t("Précédent") }}
        </button>
        <button
          class="secondary"
          :disabled="busy || orders.length < limit || offset + limit > 10000"
          @click="next(1)"
        >
          {{ t("Suivant") }}
        </button>
      </nav>
    </section>
    <DetailsPanel
      v-if="cancelChoice"
      :title="t('Annuler la commande')"
      :label="t('Confirmation d’annulation')"
      :busy="busy"
      @close="cancelChoice = null"
    >
      <p class="hint">
        {{ t("Référence") }} {{ cancelChoice.order_id }}
      </p>
      <p
        v-if="error"
        role="alert"
      >
        {{ t(error) }}
      </p>
      <p>{{ t("Les réservations seront libérées si le serveur confirme l’annulation.") }}</p>
      <div class="b2b-actions">
        <button
          :disabled="busy || pending"
          @click="cancel"
        >
          {{ t("Confirmer l’annulation") }}
        </button>
        <button
          class="secondary"
          :disabled="busy"
          @click="cancelChoice = null"
        >
          {{ t("Conserver la commande") }}
        </button>
      </div>
    </DetailsPanel>
    <DetailsPanel
      v-if="chosen"
      :title="t('Détail de la commande')"
      :return-focus="detailTrigger"
      :label="t('Détail de commande')"
      @close="chosen = null"
    >
      <p>{{ t("Référence :") }} {{ chosen.order_id }}</p>
      <p>{{ label(chosen) }} {{ t("· Acheteur") }} {{ chosen.customer_id }} {{ t("· Créateur") }} {{ chosen.created_by }}</p>
      <ul>
        <li
          v-for="line in chosen.lines"
          :key="line.product_id"
        >
          {{ line.sku }} · {{ line.quantity }} × {{ formatMoney(line.price_minor, line.currency) }}
        </li>
      </ul>
      <p class="hint">
        {{ t("Créée le") }} {{ dateLabel(chosen.created_at) }}{{ t(". Les prix ci-dessus sont ceux de la commande.") }}
      </p>
    </DetailsPanel>
  </section>
</template>
