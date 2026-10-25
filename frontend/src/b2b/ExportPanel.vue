<script setup lang="ts">
import { t } from './preferences'
import { ref } from 'vue'
import DetailsPanel from './DetailsPanel.vue'
import { useB2B } from './session'
import { useTask } from './tasks'
import { array, object, text } from './contracts'
import { downloadCsv, type CsvMode } from './csv'
const emit = defineEmits<{ close: [] }>()
const session = useB2B(); const { busy, error, run } = useTask()
const mode = ref<CsvMode>(session.has('stock:read') ? 'create_only' : 'metadata_only')
const category = ref(''); const message = ref('')
async function download() {
  await run(async () => {
    const query = new URLSearchParams({ mode: mode.value })
    if (category.value.trim()) query.set('category', category.value.trim())
    const data = await session.client.request('/b2b/api/products/export?' + query, object({ headers: array(text, 7), rows: array(array(text, 7), 1000) }))
    downloadCsv('stockpilot-' + mode.value + '.csv', [data.headers, ...data.rows])
    message.value = data.rows.length + ' ligne(s) exportée(s).'
  })
}
</script>
<template>
  <DetailsPanel
    :title="t('Exporter le catalogue')"
    :label="t('Export CSV')"
    :busy="busy"
    @close="emit('close')"
  >
    <p>{{ t("Export des produits actifs de votre organisation, au format CSV UTF-8 compatible tableur.") }}</p>
    <form
      class="product-editor"
      @submit.prevent="download"
    >
      <label>{{ t("Contenu du fichier") }}<select
        v-model="mode"
        :disabled="busy"
      >
        <option
          v-if="session.has('stock:read')"
          value="create_only"
        >{{ t("Catalogue et stock physique") }}</option>
        <option value="metadata_only">{{ t("Descriptions et prix uniquement") }}</option>
        <option
          v-if="session.has('stock:read')"
          value="stock_only"
        >{{ t("Stock physique uniquement") }}</option>
      </select></label>
      <label>{{ t("Catégorie (facultatif)") }}<input
        v-model="category"
        maxlength="128"
        :disabled="busy"
      /></label>
      <p class="hint">
        {{ t("Maximum 1 000 produits par export. Les prix sont exprimés en unités mineures ; aucune réservation n’est modifiée.") }}
      </p>
      <p class="hint">
        {{ t("Les cellules pouvant être interprétées comme des formules sont protégées par une apostrophe.") }}
      </p>
      <p
        v-if="error"
        role="alert"
      >
        {{ t(error) }}
      </p><p
        v-if="message"
        role="status"
      >
        {{ t(message) }}
      </p>
      <button :disabled="busy">
        {{ t("Télécharger le CSV") }}
      </button>
    </form>
  </DetailsPanel>
</template>
