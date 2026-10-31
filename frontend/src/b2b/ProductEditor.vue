<script setup lang="ts">
import { t } from './preferences'
import { ref, watch } from 'vue'
import DetailsPanel from './DetailsPanel.vue'
import { useB2B } from './session'
import { useTask } from './tasks'
import { mutate, physicalQuantity } from './workflows'
import { previewDecoder, resultDecoder, type Preview, type Product } from './contracts'
import { csvHeaders, encodeCsv, priceInput, priceMinor } from './csv'
const props = defineProps<{ product?: Product; remove?: boolean }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const session = useB2B(); const { busy, error, run } = useTask()
const sku = ref(props.product?.sku ?? ''); const name = ref(props.product?.name ?? '')
const description = ref(props.product?.description ?? ''); const category = ref(props.product?.category ?? '')
const attributes = ref(props.product?.attributes.join('\n') ?? '')
const currency = ref(props.product?.currency ?? 'EUR')
const price = ref(props.product ? priceInput(props.product.price_minor, props.product.currency) : '')
const quantity = ref('0'); const preview = ref<Preview | null>(null); const confirmed = ref(false)
const outcome = ref('')
let revision = 0
watch([sku, name, description, category, currency, price, quantity, attributes], () => { revision++; preview.value = null; confirmed.value = false; outcome.value = '' }, { flush: 'sync' })
async function prepare() {
  await run(async () => {
    const version = revision
    const mode = props.product ? 'metadata_only' : 'create_only'
    const row: (string | number)[] = [sku.value.trim(), name.value.trim(), description.value, category.value.trim(), priceMinor(price.value, currency.value), currency.value]
    if (!props.product) row.push(physicalQuantity(quantity.value))
    row.push(JSON.stringify(attributes.value.split('\n').map(value => value.trim()).filter(Boolean)))
    const result = await session.client.request('/b2b/api/imports/preview?mode=' + mode, previewDecoder,
      { method: 'POST', csv: encodeCsv([[...csvHeaders[mode], 'attributes'], row]) })
    if (version !== revision) return
    preview.value = result
    confirmed.value = false
  })
}
async function apply() {
  if (!confirmed.value || session.pending.value.length) return
  await run(async () => {
    let result
    if (props.remove && props.product) {
      result = await mutate(session, 'stock', 'archive', '/b2b/api/products/' + encodeURIComponent(props.product.product_id) + '/archive',
        id => ({ operation_id: id, expected_revision: props.product!.revision, confirmed: true }), resultDecoder)
    } else {
      const draft = preview.value
      if (!draft || draft.status !== 'preview' || Date.parse(draft.expires_at) <= Date.now()) throw new Error('Préparez un nouvel aperçu avant de confirmer.')
      result = await mutate(session, 'stock', 'import', '/b2b/api/imports/apply',
        id => ({ operation_id: id, import_id: draft.import_id, preview_digest: draft.preview_digest, confirmed: true }), resultDecoder)
    }
    confirmed.value = false; preview.value = null
    if (result.status === 'succeeded') emit('saved')
    else outcome.value = result.status === 'pending' ? 'Résultat à vérifier avant de recommencer.' : 'Modification refusée : ' + result.code
  })
}
</script>
<template>
  <DetailsPanel
    :title="remove ? t('Retirer le produit du catalogue') : product ? t('Modifier le produit') : t('Ajouter un produit')"
    :label="t('Gestion du produit')"
    :busy="busy"
    @close="emit('close')"
  >
    <p
      v-if="error"
      role="alert"
    >
      {{ t(error) }}
    </p>
    <p
      v-if="outcome"
      role="status"
    >
      {{ t(outcome) }}
    </p>
    <template v-if="remove">
      <p><strong>{{ product?.name }}</strong> · {{ product?.sku }}</p>
      <p>{{ t("Le produit ne sera plus proposé à la vente. Ses stocks et son historique de commandes seront conservés.") }}</p>
      <p class="hint">
        {{ t("Le retrait est refusé si une quantité est encore réservée.") }}
      </p>
    </template>
    <form
      v-else
      class="product-editor"
      @submit.prevent="prepare"
    >
      <div class="b2b-grid">
        <label>{{ t("Référence SKU") }}<input
          v-model="sku"
          :readonly="!!product"
          :disabled="busy"
          required
          maxlength="128"
        /></label>
        <label>{{ t("Nom du produit") }}<input
          v-model="name"
          :disabled="busy"
          required
          maxlength="200"
        /></label>
      </div>
      <label>{{ t("Description") }}<textarea
        v-model="description"
        :disabled="busy"
        maxlength="2000"
      ></textarea></label>
      <div class="b2b-grid">
        <label>{{ t("Caractéristiques (une par ligne, 32 maximum)") }}
          <textarea
            v-model="attributes"
            maxlength="4127"
            rows="4"
          ></textarea>
        </label>
        <label>{{ t("Catégorie") }}<input
          v-model="category"
          :disabled="busy"
          required
          maxlength="128"
        /></label>
        <label>{{ t("Devise") }}<select
          v-model="currency"
          :disabled="busy || !!product"
        ><option>EUR</option><option>USD</option><option>GBP</option><option>CHF</option><option>TND</option></select></label>
        <label>{{ t("Prix unitaire") }}<input
          v-model="price"
          :disabled="busy"
          inputmode="decimal"
          required
        /></label>
        <label v-if="!product">{{ t("Stock physique initial") }}<input
          v-model="quantity"
          :disabled="busy"
          inputmode="numeric"
          required
        /></label>
      </div>
      <p
        v-if="product"
        class="hint"
      >
        {{ t("Cette modification ne change ni le stock physique ni les réservations.") }}
      </p>
      <button :disabled="busy || !!session.pending.value.length">
        {{ t("Vérifier les modifications") }}
      </button>
    </form>
    <section
      v-if="preview"
      class="b2b-notice"
      :aria-label="t('Vérification du produit')"
    >
      <p>{{ preview.status === 'preview' ? t("Modifications vérifiées. Vous pouvez confirmer.") : t("Corrigez les informations ci-dessous.") }}</p>
      <ul v-if="preview.issues.length">
        <li
          v-for="(issue, index) in preview.issues"
          :key="index"
        >
          {{ issue.message }}
        </li>
      </ul>
    </section>
    <div
      v-if="remove || preview?.status === 'preview'"
      class="dialog-confirmation"
    >
      <label class="check-label"><input
        v-model="confirmed"
        type="checkbox"
        :disabled="busy"
      /> {{ t("Je confirme cette modification.") }}</label>
      <div class="b2b-actions">
        <button
          :class="{ danger: remove }"
          :disabled="busy || !confirmed || !!session.pending.value.length"
          @click="apply"
        >
          {{ remove ? t("Retirer du catalogue") : t("Enregistrer le produit") }}
        </button>
        <button
          class="secondary"
          :disabled="busy"
          @click="emit('close')"
        >
          {{ t("Annuler") }}
        </button>
      </div>
    </div>
  </DetailsPanel>
</template>
