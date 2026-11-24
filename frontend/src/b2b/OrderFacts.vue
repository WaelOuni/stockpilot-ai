<script setup lang="ts">
import { computed } from 'vue'
import type { Order } from './contracts'
import { formatMoney, formatMoneyTotal } from './money'
import { dateLabel, t } from './preferences'
const props = defineProps<{ order: Order; organization: string }>()
const totals = computed(() => {
  const result = new Map<string, bigint>()
  for (const line of props.order.lines) result.set(line.currency, (result.get(line.currency) ?? 0n) + BigInt(line.price_minor) * BigInt(line.quantity))
  return [...result].map(([currency, minor]) => ({ currency, minor }))
})
const events = computed(() => [
  { label: 'Préparation enregistrée', date: props.order.created_at },
  { label: 'Stock réservé', date: props.order.reserved_at },
  { label: 'Commande annulée', date: props.order.cancelled_at },
].filter((item): item is { label: string; date: string } => item.date !== null))
const status = computed(() => ({ prepared: 'Préparée — aucune réservation', reserved: 'Réservée', cancelled: 'Annulée' })[props.order.status])
</script>
<template>
  <div class="order-overview">
    <div>
      <span class="eyebrow">{{ t('Référence') }}</span><p class="order-reference">
        {{ order.order_id }}
      </p>
    </div>
    <span
      class="status-badge"
      :data-status="order.status"
    >{{ t(status) }}</span>
  </div>
  <dl class="facts-grid">
    <div><dt>{{ t('Organisation') }}</dt><dd>{{ organization }}</dd></div>
    <div><dt>{{ t('Créée par') }}</dt><dd>{{ order.created_by }}</dd></div>
    <div><dt>{{ t('Acheteur') }}</dt><dd>{{ order.customer_id }}</dd></div>
    <div><dt>{{ t('Créée le') }}</dt><dd>{{ dateLabel(order.created_at) }}</dd></div>
    <div><dt>{{ t('Dernière mise à jour') }}</dt><dd>{{ t('Non disponible') }}</dd></div>
    <div><dt>{{ t('Réservation') }}</dt><dd>{{ t(order.status === 'reserved' ? 'Stock réservé pour cette commande' : order.status === 'cancelled' && order.reserved_at ? 'Réservation libérée' : 'Aucun stock réservé') }}</dd></div>
  </dl>
  <h3>{{ t('Produits et quantités') }}</h3>
  <p class="hint">
    {{ t('Les SKU identifient les produits. Les noms ne sont pas conservés dans cet historique.') }}
  </p>
  <p class="hint order-scroll-hint">
    {{ t('Sur petit écran, faites défiler le tableau pour voir les totaux des lignes.') }}
  </p>
  <div
    class="order-lines b2b-table"
    role="region"
    :aria-label="t('Produits de la commande')"
    tabindex="0"
  >
    <table>
      <caption class="sr-only">
        {{ t('Produits de la commande') }}
      </caption>
      <thead>
        <tr>
          <th scope="col">
            SKU
          </th><th scope="col">
            {{ t('Quantité') }}
          </th><th scope="col">
            {{ t('Prix unitaire') }}
          </th><th scope="col">
            {{ t('Total des lignes') }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="line in order.lines"
          :key="line.product_id"
        >
          <th scope="row">
            {{ line.sku }}
          </th><td>{{ line.quantity }}</td><td>{{ formatMoney(line.price_minor, line.currency) }}</td><td>{{ formatMoneyTotal(BigInt(line.price_minor) * BigInt(line.quantity), line.currency) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="order-totals">
    <p
      v-for="total in totals"
      :key="total.currency"
    >
      <span>{{ t('Total des lignes') }} · {{ total.currency }}</span><strong>{{ formatMoneyTotal(total.minor, total.currency) }}</strong>
    </p>
    <p class="hint">
      {{ t('Calculé à partir des prix enregistrés. Taxes, livraison et remises non renseignées.') }}
    </p>
  </div>
  <h3>{{ t('Événements enregistrés') }}</h3>
  <ol class="order-events">
    <li
      v-for="event in events"
      :key="event.label"
    >
      <strong>{{ t(event.label) }}</strong><time :datetime="event.date">{{ dateLabel(event.date) }}</time>
    </li>
  </ol>
  <p class="hint">
    {{ t('Seules les dates présentes dans la commande sont affichées.') }}
  </p>
  <p
    v-if="order.status === 'prepared'"
    class="hint"
  >
    {{ t('Préparation valable jusqu’au') }} {{ dateLabel(order.expires_at) }}
  </p>
</template>
