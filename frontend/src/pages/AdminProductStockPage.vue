<script setup lang="ts">
import Pagination from "../components/BasePagination.vue"
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import {
  adjustAdminProductStock,
  getAdminProduct,
  getAdminStockAdjustment,
  listAdminStockMovements,
} from '../api/admin'
import { ApiError } from '../api/http'
import type { ProductResponse, StockMovement, StockMovementsResponse } from '../types/api'
import {
  clearPendingStockAdjustment,
  readPendingStockAdjustment,
  savePendingStockAdjustment,
} from '../utils/pendingStockAdjustment'
import AdminLogout from '../components/AdminLogout.vue'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'

interface AdjustmentRequest {
  sku: string
  operationId: string
  delta: number
  reason: string
}

const route = useRoute()
const router = useRouter()
const sku = computed(() => typeof route.params.sku === 'string' ? route.params.sku : '')
const product = ref<ProductResponse | null>(null)
const movements = ref<StockMovementsResponse | null>(null)
const deltaDraft = ref('')
const reasonDraft = ref('')
const preview = ref<Pick<AdjustmentRequest, 'delta' | 'reason'> | null>(null)
const confirmed = ref(false)
const pendingOperation = ref<AdjustmentRequest | null>(null)
const operationResult = ref<StockMovement | null>(null)
const loading = ref(false)
const submitting = ref(false)
const reconciling = ref(false)
const blocked = ref(false)
const storageUnavailable = ref(false)
const loggingOut = ref(false)
const error = ref<Error | null>(null)
const feedback = ref('')
let controller: AbortController | undefined
let timeout: ReturnType<typeof setTimeout> | undefined
let pageGeneration = 0
const offset = ref(0)

const estimatedStock = computed(() => product.value && preview.value
  ? product.value.stock + preview.value.delta : null)
const canPreview = computed(() => {
  if (!product.value || loading.value || submitting.value || reconciling.value || blocked.value || loggingOut.value) return false
  if (!/^-?(0|[1-9]\d*)$/.test(deltaDraft.value.trim())) return false
  const delta = Number(deltaDraft.value)
  return Number.isSafeInteger(delta) && delta !== 0 && Math.abs(delta) <= 1_000_000
    && product.value.stock + delta >= 0 && reasonDraft.value.trim().length > 0
    && reasonDraft.value.trim().length <= 500
})

function startRequest() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  return { request, generation: pageGeneration }
}

function current(request: AbortController, generation: number) {
  return !request.signal.aborted && controller === request && generation === pageGeneration
}

function report(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) {
    void router.replace('/admin/login?reason=session')
  } else {
    error.value = cause instanceof Error ? cause : new Error('Opération de stock impossible.')
  }
}

async function loadPage(request: AbortController, generation: number) {
  const [freshProduct, freshMovements] = await Promise.all([
    getAdminProduct(sku.value, request.signal),
    listAdminStockMovements(sku.value, offset.value, request.signal),
  ])
  if (!current(request, generation)) return
  if (freshMovements.total !== undefined && offset.value > 0 && offset.value >= freshMovements.total) {
    offset.value = Math.max(0, Math.floor((freshMovements.total - 1) / 20) * 20)
    await loadPage(request, generation)
    return
  }
  product.value = freshProduct
  movements.value = freshMovements
}

async function refresh() {
  if (submitting.value || reconciling.value || loggingOut.value) return
  if (!storageUnavailable.value) error.value = null
  const { request, generation } = startRequest()
  loading.value = true
  movements.value = null
  try {
    await loadPage(request, generation)
    if (current(request, generation) && !pendingOperation.value && !storageUnavailable.value) blocked.value = false
  } catch (cause) {
    if (current(request, generation)) report(cause)
  } finally {
    if (current(request, generation)) loading.value = false
  }
}

function preparePreview() {
  if (!canPreview.value) {
    error.value = new Error('Delta entier non nul, motif de 1 à 500 caractères et stock final estimé positif ou nul requis.')
    return
  }
  preview.value = { delta: Number(deltaDraft.value.trim()), reason: reasonDraft.value.trim() }
  confirmed.value = false
  error.value = null
  feedback.value = ''
}

function cancelPreview() {
  if (submitting.value) return
  preview.value = null
  confirmed.value = false
}

async function refreshAfterKnownCommit(request: AbortController, generation: number) {
  try {
    await loadPage(request, generation)
    if (current(request, generation)) blocked.value = false
  } catch (cause) {
    if (current(request, generation)) {
      blocked.value = true
      report(cause)
    }
  }
}

async function reconcile(knownRejected = false) {
  const pending = pendingOperation.value
  if (!pending || pending.sku !== sku.value || reconciling.value || loggingOut.value) return
  const { request, generation } = startRequest()
  reconciling.value = true
  error.value = null
  let found: StockMovement | null = null
  let operationMissing = false
  let operationError: unknown
  let productError: unknown
  let historyError: unknown
  const [operation, stock, history] = await Promise.allSettled([
    getAdminStockAdjustment(pending.operationId, request.signal),
    getAdminProduct(sku.value, request.signal),
    listAdminStockMovements(sku.value, offset.value, request.signal),
  ])
  if (!current(request, generation)) return
  if (operation.status === 'fulfilled') found = operation.value
  else if (operation.reason instanceof ApiError && operation.reason.status === 404) operationMissing = true
  else operationError = operation.reason
  if (stock.status === 'fulfilled') product.value = stock.value
  else productError = stock.reason
  if (history.status === 'fulfilled') movements.value = history.value
  else historyError = history.reason

  try {
    if (found) {
      if (found.product_sku !== sku.value || found.delta !== pending.delta || found.reason !== pending.reason) {
        throw new ApiError('L’identifiant d’opération correspond à un ajustement différent.', 409)
      }
      operationResult.value = found
      clearPendingStockAdjustment()
      pendingOperation.value = null
      preview.value = null
      confirmed.value = false
      feedback.value = 'Ajustement retrouvé dans l’historique. Le stock affiché provient du serveur.'
      blocked.value = Boolean(productError || historyError)
      if (productError) report(productError)
      else if (historyError) report(historyError)
      return
    }
    if (operationMissing && knownRejected && stock.status === 'fulfilled') {
      clearPendingStockAdjustment()
      pendingOperation.value = null
      preview.value = null
      confirmed.value = false
      blocked.value = false
      feedback.value = 'Le serveur a refusé l’ajustement et aucune opération correspondante n’est visible. Le stock a été relu ; aucune nouvelle requête n’a été envoyée.'
      error.value = new Error('Ajustement refusé. Modifiez le delta ou le motif avant de prévisualiser une nouvelle opération.')
      if (historyError) report(historyError)
      return
    }
    blocked.value = true
    if (productError) report(productError)
    else if (operationError) report(operationError)
    else if (historyError) report(historyError)
    else error.value = new Error('Résultat toujours inconnu : aucun mouvement visible ne prouve un échec. Réessayez uniquement la vérification.')
  } catch (cause) {
    blocked.value = true
    report(cause)
  } finally {
    if (current(request, generation)) reconciling.value = false
  }
}

async function confirmAdjustment() {
  if (!preview.value || !confirmed.value || submitting.value || loading.value || reconciling.value
    || blocked.value || loggingOut.value) return
  const pending: AdjustmentRequest = {
    sku: sku.value,
    operationId: crypto.randomUUID(),
    delta: preview.value.delta,
    reason: preview.value.reason,
  }
  try {
    savePendingStockAdjustment(pending)
  } catch {
    blocked.value = true
    error.value = new Error('Impossible de conserver l’identifiant d’opération. Aucun ajustement n’a été envoyé.')
    return
  }
  pendingOperation.value = pending
  operationResult.value = null
  blocked.value = true
  submitting.value = true
  error.value = null
  feedback.value = ''
  const { request, generation } = startRequest()
  const requestTimeout = setTimeout(() => request.abort(), 20_000)
  timeout = requestTimeout
  let failureCause: unknown
  try {
    const movement = await adjustAdminProductStock(
      sku.value, pending.operationId, pending.delta, pending.reason, request.signal,
    )
    if (!current(request, generation)) return
    operationResult.value = movement
    clearPendingStockAdjustment()
    pendingOperation.value = null
    preview.value = null
    confirmed.value = false
    feedback.value = `Ajustement enregistré : stock ${movement.stock_before} → ${movement.stock_after}.`
    await refreshAfterKnownCommit(request, generation)
  } catch (cause) {
    if (controller !== request || generation !== pageGeneration) return
    failureCause = request.signal.aborted
      ? new ApiError('Délai dépassé. Vérifiez le résultat sans renvoyer la mutation.', 0)
      : cause
  } finally {
    clearTimeout(requestTimeout)
    if (timeout === requestTimeout) timeout = undefined
    if (controller === request && generation === pageGeneration) submitting.value = false
  }
  if (failureCause && controller === request && generation === pageGeneration) {
    report(failureCause)
    if (!(failureCause instanceof ApiError && failureCause.status === 401)) {
      await reconcile(failureCause instanceof ApiError && failureCause.status === 409)
    }
  }
}

function navigateHistory(newOffset: number) {
  if (loading.value || submitting.value || reconciling.value || pendingOperation.value) return
  offset.value = Math.max(0, newOffset)
  void refresh()
}

function logoutPending(pending: boolean) {
  loggingOut.value = pending
  if (pending) {
    blocked.value = true
    controller?.abort()
    clearTimeout(timeout)
  }
}

watch(() => route.fullPath, () => {
  pageGeneration++
  controller?.abort()
  clearTimeout(timeout)
  timeout = undefined
  product.value = null
  movements.value = null
  preview.value = null
  confirmed.value = false
  error.value = null
  storageUnavailable.value = false
  try {
    pendingOperation.value = readPendingStockAdjustment()
    blocked.value = Boolean(pendingOperation.value)
  } catch (cause) {
    pendingOperation.value = null
    blocked.value = true
    storageUnavailable.value = true
    error.value = cause instanceof Error ? cause : new Error('Rapprochement local indisponible.')
  }
  operationResult.value = null
  deltaDraft.value = ''
  reasonDraft.value = ''
  offset.value = 0
  loading.value = false
  submitting.value = false
  reconciling.value = false
  feedback.value = ''
  void refresh().then(() => {
    if (pendingOperation.value?.sku === sku.value && !error.value) void reconcile()
  })
}, { immediate: true })

onBeforeUnmount(() => {
  pageGeneration++
  controller?.abort()
  clearTimeout(timeout)
})
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div>
      <span class="eyebrow">Back-office · Ajustement audité</span>
      <h1>Ajuster le stock</h1>
    </div>
    <div class="header-actions">
      <AdminLogout @pending="logoutPending" />
    </div>
  </header>
  <RouterLink to="/admin/products">
    Retour aux produits
  </RouterLink>
  <p
    v-if="product"
    class="product-stock-summary identifier-value"
  >
    {{ product.product_name }} · SKU {{ product.sku }} · stock serveur :
    <strong>{{ product.stock }}</strong>
  </p>
  <p
    v-else-if="loading"
    role="status"
  >
    Lecture du produit…
  </p>
  <form
    :aria-describedby="error ? 'form-error' : undefined"
    @submit.prevent="preparePreview"
  >
    <fieldset :disabled="loading || submitting || reconciling || blocked || loggingOut || !product || Boolean(preview)">
      <label for="stock-delta">Variation de stock signée</label>
      <input
        id="stock-delta"
        v-model="deltaDraft"
        type="text"
        inputmode="numeric"
        required
      />
      <label for="stock-reason">Motif (obligatoire)</label>
      <textarea
        id="stock-reason"
        v-model="reasonDraft"
        maxlength="500"
        required
      ></textarea>
      <button type="submit">
        Prévisualiser
      </button>
    </fieldset>
  </form>
  <section
    v-if="preview && product"
    aria-label="Prévisualisation de l’ajustement"
    class="context-card confirmation-panel"
  >
    <h2>Confirmer l’ajustement</h2>
    <p>Variation : {{ preview.delta > 0 ? '+' : '' }}{{ preview.delta }}</p>
    <p>Stock estimé : {{ product.stock }} → {{ estimatedStock }}</p>
    <p>Motif : {{ preview.reason }}</p>
    <label>
      <input
        v-model="confirmed"
        type="checkbox"
      />
      Je confirme cet ajustement de stock.
    </label>
    <div class="context-actions">
      <button
        :disabled="!confirmed || submitting || blocked || loggingOut"
        @click="confirmAdjustment"
      >
        {{ submitting ? 'Enregistrement…' : 'Confirmer l’ajustement' }}
      </button>
      <button
        :disabled="submitting"
        class="secondary"
        @click="cancelPreview"
      >
        Modifier
      </button>
    </div>
  </section>
  <p
    v-if="feedback && !loading"
    :class="error ? 'info-notice' : 'success-notice'"
    role="status"
  >
    {{ feedback }}
  </p>
  <ApiErrorNotice
    v-if="error"
    id="form-error"
    :error="error"
  />
  <div
    v-if="pendingOperation"
    class="context-card"
  >
    <p>Opération {{ pendingOperation.operationId }} en vérification. Ne soumettez pas un nouvel ajustement.</p>
    <RouterLink
      v-if="pendingOperation.sku !== sku"
      :to="`/admin/products/${encodeURIComponent(pendingOperation.sku)}/stock`"
    >
      Revenir au produit dont l’opération est incertaine
    </RouterLink>
    <button
      v-if="pendingOperation.sku === sku"
      :disabled="submitting || reconciling || loggingOut"
      @click="reconcile()"
    >
      {{ reconciling ? 'Vérification…' : 'Vérifier le résultat' }}
    </button>
  </div>
  <section
    v-if="operationResult"
    aria-label="Résultat de l’ajustement"
    class="context-card"
  >
    <p>{{ operationResult.delta > 0 ? '+' : '' }}{{ operationResult.delta }} · stock {{ operationResult.stock_before }} → {{ operationResult.stock_after }}</p>
    <p>{{ operationResult.reason }}</p>
  </section>
  <section aria-label="Historique des mouvements">
    <div class="section-heading">
      <h2>Historique des mouvements</h2>
      <button
        class="secondary"
        :disabled="loading || submitting || reconciling"
        @click="pendingOperation ? reconcile() : refresh()"
      >
        Actualiser
      </button>
    </div>
    <p
      v-if="loading"
      role="status"
    >
      Chargement de l’historique…
    </p>
    <p v-else-if="movements && !movements.items.length">
      Aucun ajustement de stock enregistré.
    </p>
    <article
      v-for="movement in movements?.items ?? []"
      :key="movement.movement_id"
      class="context-card"
    >
      <p>{{ movement.delta > 0 ? '+' : '' }}{{ movement.delta }} · {{ movement.stock_before }} → {{ movement.stock_after }}</p>
      <p>{{ movement.reason }}</p>
      <time :datetime="movement.occurred_at">{{ new Date(movement.occurred_at).toLocaleString('fr-FR') }}</time>
      <p>{{ movement.source }}</p>
    </article>
    <Pagination
      :offset="offset"
      :total="movements?.total"
      :count="movements?.count"
      :has-more="movements?.has_more ?? false"
      :loading="loading || submitting || reconciling || Boolean(pendingOperation)"
      label="mouvements"
      @change="navigateHistory"
    />
  </section>
</template>
