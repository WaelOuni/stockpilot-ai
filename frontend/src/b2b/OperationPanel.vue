<script setup lang="ts">
import { t } from './preferences'
import { ref, watch } from 'vue'
import { useB2B, type OperationRef } from './session'
import { orderDecoder, resultDecoder } from './contracts'
import { useTask } from './tasks'
const session = useB2B()
const { pending, current } = session
const { busy, error, run } = useTask()
const message = ref('')
watch(() => pending.value.length, (length, before) => { if (length > before) message.value = '' }, { flush: 'sync' })
async function check(op: OperationRef) {
  await run(async () => {
    const query = new URLSearchParams({ namespace: op.namespace, operation_id: op.id })
    if (op.family === 'orders') {
      const result = await session.client.request('/b2b/api/orders/operations?' + query, orderDecoder)
      message.value = 'Commande ' + result.order_id + ' : ' + ({ prepared: 'préparée, non réservée', reserved: 'réservée', cancelled: 'annulée' }[result.status])
      session.resolve(op); session.businessRevision.value++
    } else {
      const result = await session.client.request('/b2b/api/stock/operations?' + query, resultDecoder)
      message.value = result.status === 'pending' ? 'Vérification toujours en cours. Conservez cette référence.' : result.status === 'succeeded' ? 'Opération enregistrée.' : 'Opération refusée : ' + result.code
      if (result.status !== 'pending') { session.resolve(op); session.businessRevision.value++ }
    }
  })
}
</script>
<template>
  <section
    v-if="current && (pending.length || message)"
    class="b2b-notice"
    :aria-label="t('Vérification des opérations')"
  >
    <h2>{{ t("Vérifier avant de recommencer") }}</h2>
    <p>{{ t("Aucun nouvel envoi automatique. Un résultat introuvable ne prouve pas une annulation.") }}</p>
    <p
      v-if="message"
      role="status"
    >
      {{ t(message) }} {{ t("Actualisez la liste concernée pour consulter l’état courant.") }}
    </p>
    <p
      v-if="error"
      role="alert"
    >
      {{ t(error) }} {{ t("La référence reste conservée.") }}
    </p>
    <ul>
      <li
        v-for="operation in pending"
        :key="operation.id"
      >
        <code>{{ operation.namespace }} · {{ operation.id }}</code>
        <button
          type="button"
          class="secondary"
          :disabled="busy"
          @click="check(operation)"
        >
          {{ t("Vérifier le résultat") }}
        </button>
      </li>
    </ul>
  </section>
</template>
