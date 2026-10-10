<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import ApiErrorNotice from '../../components/ApiErrorNotice.vue'
import { customerToken } from '../../utils/customerSession'
import { useAssistant } from './useAssistant'

const { state, send, reset } = useAssistant()
const draft = ref('')
const transcript = ref<HTMLElement | null>(null)
watch(customerToken, () => { draft.value = '' })
watch(() => state.messages.length, async () => {
  await nextTick()
  transcript.value?.scrollTo?.({ top: transcript.value.scrollHeight, behavior: 'smooth' })
})
async function submit() {
  if (!draft.value.trim() || state.loading || !customerToken.value) return
  const message = draft.value
  draft.value = ''
  await send(message)
}
function newConversation() {
  draft.value = ''
  reset()
}
</script>

<template>
  <section
    class="conversation card"
    aria-label="Conversation assistant"
  >
    <div class="conversation-toolbar">
      <span class="eyebrow">Conseil, stock et commandes</span>
      <button
        class="secondary"
        type="button"
        :disabled="state.loading"
        @click="newConversation"
      >
        Nouvelle conversation
      </button>
    </div>
    <div
      ref="transcript"
      class="transcript"
      role="log"
      aria-live="polite"
      aria-relevant="additions"
    >
      <div
        v-if="!state.messages.length"
        class="empty-state"
      >
        <h2>Quel outil pour votre prochain projet ?</h2>
        <p>Demandez un conseil, verifiez le stock ou preparez une commande.</p>
        <p class="hint">
          Exemple : « Je veux commander 2 Bosch X100 »
        </p>
      </div>
      <article
        v-for="message in state.messages"
        :key="message.id"
        class="message"
        :class="message.role"
      >
        <span class="message-author">{{ message.role === 'customer' ? 'Vous' : 'StockPilot' }}</span>
        <p>{{ message.text }}</p>
      </article>
      <p
        v-if="state.loading"
        class="loading"
        role="status"
      >
        StockPilot prepare sa reponse...
      </p>
    </div>
    <div
      v-if="state.result?.order_status === 'PENDING_CONFIRMATION'"
      class="order-prompt"
    >
      Une commande attend votre decision. Repondez explicitement pour confirmer ou abandonner.
    </div>
    <ApiErrorNotice :error="state.error" />
    <p
      v-if="state.error"
      class="hint"
    >
      Aucune relance automatique. Une confirmation peut avoir ete traitee : demandez le statut avant de recommencer.
    </p>
    <form
      class="composer"
      @submit.prevent="submit"
    >
      <label for="assistant-message">Votre message</label>
      <textarea
        id="assistant-message"
        v-model="draft"
        rows="3"
        placeholder="Posez votre question..."
        :disabled="state.loading || !customerToken"
      ></textarea>
      <div class="composer-footer">
        <small>{{ customerToken ? 'Conversation conservee pendant cette session.' : 'Connectez un JWT client pour commencer.' }}</small>
        <button
          :disabled="state.loading || !customerToken || !draft.trim()"
          type="submit"
        >
          Envoyer
        </button>
      </div>
    </form>
  </section>
</template>
