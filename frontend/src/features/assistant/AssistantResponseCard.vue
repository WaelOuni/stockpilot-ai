<script setup lang="ts">
import { computed } from 'vue'
import type { AssistantResponse } from '../../types/api'
import ProductRecommendationCard from '../../components/ProductRecommendationCard.vue'
import OrderConfirmationCard from '../../components/OrderConfirmationCard.vue'
import OrderSummaryCard from '../../components/OrderSummaryCard.vue'
import StockAvailabilityCard from '../../components/StockAvailabilityCard.vue'
import { isOrderStatus } from '../../utils/format'

const props = defineProps<{ result: AssistantResponse; actionable: boolean }>()
defineEmits<{ confirm: []; cancel: [] }>()
const pending = computed(() => {
  const value = props.result.pending_order
  if (value && typeof value.product_name === 'string' && value.product_name.trim() &&
      typeof value.quantity === 'number' && Number.isInteger(value.quantity) && value.quantity > 0 &&
      typeof value.operation_id === 'string' && value.operation_id &&
      props.result.order_status === value.status) {
    return { productName: value.product_name, quantity: value.quantity }
  }
  return null
})
const foundStatus = computed(() =>
  isOrderStatus(props.result.queried_order_status) ? props.result.queried_order_status : undefined)
</script>

<template>
  <div v-if="result.intent === 'PRODUCT_ADVICE' && result.advice_status === 'RESULTS' && !result.confirmation_handled">
    <ProductRecommendationCard
      v-for="product in result.recommendations || []"
      :key="product.sku"
      :product="product"
    />
  </div>
  <OrderSummaryCard
    v-if="result.intent === 'ORDER_STATUS' && result.order_status_result === 'FOUND' && result.queried_order_id && foundStatus"
    title="Statut de la commande"
    :order-id="result.queried_order_id"
    :status="foundStatus"
  />
  <OrderSummaryCard
    v-else-if="result.confirmation_handled === true && result.order_status === 'CONFIRMED' && result.created_order_id"
    title="Commande créée"
    :order-id="result.created_order_id"
    :product-name="pending?.productName"
    :quantity="pending?.quantity"
  >
    <small>Commande enregistrée. Consultez vos commandes pour son statut actuel.</small>
  </OrderSummaryCard>
  <OrderConfirmationCard
    v-else-if="result.intent === 'ORDER' && result.order_status === 'PENDING_CONFIRMATION' && pending"
    :product-name="pending.productName"
    :quantity="pending.quantity"
    :disabled="!actionable"
    @confirm="$emit('confirm')"
    @cancel="$emit('cancel')"
  />
  <StockAvailabilityCard
    v-else-if="result.intent === 'ORDER' && result.order_status === 'INSUFFICIENT_STOCK'"
    :product-name="result.product_name"
    :quantity="result.quantity"
  />
</template>
