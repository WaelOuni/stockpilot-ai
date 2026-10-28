<script setup lang="ts">
import { t } from './preferences'
import { onMounted, ref, useId } from 'vue'
import { RouterLink } from 'vue-router'
import DetailsPanel from './DetailsPanel.vue'
import { useB2B } from './session'
import { useTask } from './tasks'
import { productDecoder, stockDecoder, type Product, type Stock } from './contracts'
import { formatMoney } from './money'
const props = defineProps<{ ids: string[] }>(); const emit = defineEmits<{ close: [] }>()
const session = useB2B(); const { busy, error, run } = useTask()
const products = ref<Product[]>([]); const stocks = ref<Record<string, Stock>>({})
const scrollHintId = useId()
onMounted(() => { void run(async () => {
  for (const id of [...new Set(props.ids)].slice(0, 4)) {
    const item = await session.client.request('/b2b/api/products/' + encodeURIComponent(id), productDecoder)
    products.value.push(item)
    if (session.has('stock:read')) stocks.value[id] = await session.client.request('/b2b/api/products/' + encodeURIComponent(id) + '/stock', stockDecoder)
  }
}) })
</script>
<template>
  <DetailsPanel
    :title="t('Comparer les produits')"
    :label="t('Comparatif produits')"
    @close="emit('close')"
  >
    <p class="hint">
      {{ t("Prix et disponibilités actualisés à l’ouverture. Une comparaison ne réserve aucun stock.") }}
    </p>
    <p
      v-if="busy"
      role="status"
    >
      {{ t("Chargement du comparatif…") }}
    </p><p
      v-if="error"
      role="alert"
    >
      {{ t(error) }}
    </p>
    <p
      v-if="products.length"
      :id="scrollHintId"
      class="hint"
    >
      {{ t("Faites défiler horizontalement pour voir tous les produits. Au clavier, utilisez les flèches gauche et droite.") }}
    </p>
    <div
      v-if="products.length"
      class="b2b-table comparison-table"
      role="region"
      :aria-label="t('Comparatif produits')"
      :aria-describedby="scrollHintId"
      tabindex="0"
    >
      <table>
        <caption class="sr-only">
          {{ t("Comparatif des produits sélectionnés") }}
        </caption>
        <thead>
          <tr>
            <th scope="col">
              {{ t("Critère") }}
            </th><th
              v-for="product in products"
              :key="product.product_id"
              scope="col"
            >
              {{ product.name }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">
              {{ t("Référence") }}
            </th><td
              v-for="product in products"
              :key="product.product_id"
            >
              {{ product.sku }}
            </td>
          </tr>
          <tr>
            <th scope="row">
              {{ t("Prix unitaire") }}
            </th><td
              v-for="product in products"
              :key="product.product_id"
              class="b2b-price"
            >
              {{ formatMoney(product.price_minor, product.currency) }}
            </td>
          </tr>
          <tr>
            <th scope="row">
              {{ t("Catégorie") }}
            </th><td
              v-for="product in products"
              :key="product.product_id"
            >
              {{ product.category }}
            </td>
          </tr>
          <tr v-if="session.has('stock:read')">
            <th scope="row">
              {{ t("Disponible") }}
            </th><td
              v-for="product in products"
              :key="product.product_id"
            >
              {{ stocks[product.product_id]?.available ?? '—' }}
            </td>
          </tr>
          <tr>
            <th scope="row">
              {{ t("Description") }}
            </th><td
              v-for="product in products"
              :key="product.product_id"
            >
              {{ product.description }}
            </td>
          </tr>
          <tr>
            <th scope="row">
              {{ t("Caractéristiques") }}
            </th><td
              v-for="product in products"
              :key="product.product_id"
            >
              {{ product.attributes.join(' · ') || '—' }}
            </td>
          </tr>
          <tr v-if="session.has('order:create')">
            <th scope="row">
              {{ t("Commander") }}
            </th><td
              v-for="product in products"
              :key="product.product_id"
            >
              <RouterLink
                v-if="product.status === 'active'"
                :to="{ path: '/b2b-app/orders', query: { sku: product.sku } }"
                @click="emit('close')"
              >
                {{ t("Préparer une commande") }}
              </RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </DetailsPanel>
</template>
