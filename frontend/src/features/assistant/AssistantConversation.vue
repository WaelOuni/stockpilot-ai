<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import ApiErrorNotice from '../../components/ApiErrorNotice.vue'
import { customerToken } from '../../utils/customerSession'
import { useAssistant } from './useAssistant'
import AssistantResponseCard from './AssistantResponseCard.vue'

const { state, send, reset, decide } = useAssistant()
const props = defineProps<{ initialMessage?: string }>()
const draft = ref('')
const transcript = ref<HTMLElement | null>(null)
const nearLatest = ref(true)
const unreadResponse = ref(false)
const hasAdvice = computed(() => state.messages.some(message =>
  message.result?.intent === 'PRODUCT_ADVICE' && message.result.advice_status === 'RESULTS'))
function trackScroll() {
  const element = transcript.value
  if (!element) return
  nearLatest.value = element.scrollHeight - element.scrollTop - element.clientHeight <= 64
  if (nearLatest.value) unreadResponse.value = false
}
function showLatest() {
  nearLatest.value = true
  unreadResponse.value = false
  transcript.value?.scrollTo?.({ top: transcript.value.scrollHeight, behavior: 'auto' })
}
function isActionable(id: string) {
  return id === state.messages.at(-1)?.id && !state.loading && !state.error &&
    !state.decisionAttempted && !!customerToken.value
}
function decideForMessage(id: string, decision: 'confirm' | 'cancel') {
  if (isActionable(id)) return decide(decision)
}
watch(() => props.initialMessage, (message) => { draft.value = message || '' }, { immediate: true })
watch(customerToken, (token, previous) => {
  draft.value = token && !previous ? props.initialMessage || '' : ''
})
watch(() => state.messages.length, async () => {
  if (!state.messages.length) {
    nearLatest.value = true
    unreadResponse.value = false
    return
  }
  const follow = nearLatest.value
  await nextTick()
  if (follow && nearLatest.value) showLatest()
  else if (state.messages.at(-1)?.role === 'assistant') unreadResponse.value = true
})
onMounted(showLatest)
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
    <p
      v-if="hasAdvice"
      class="catalogue-convention hint"
    >
      Les prix du catalogue sont affichés sans devise renseignée.
    </p>
    <div
      ref="transcript"
      class="transcript"
      role="log"
      aria-label="Historique de la conversation"
      aria-live="polite"
      aria-relevant="additions"
      tabindex="0"
      @scroll="trackScroll"
    >
      <div
        v-if="!state.messages.length"
        class="empty-state"
      >
        <h2>Quel outil pour votre prochain projet ?</h2>
        <p>Demandez un conseil, vérifiez le stock ou préparez une commande.</p>
        <p class="hint">
          Exemple : « Je cherche une perceuse disponible. »
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
        <AssistantResponseCard
          v-if="message.result"
          :result="message.result"
          :actionable="isActionable(message.id)"
          @confirm="decideForMessage(message.id, 'confirm')"
          @cancel="decideForMessage(message.id, 'cancel')"
        />
      </article>
      <p
        v-if="state.loading"
        class="loading"
        role="status"
      >
        StockPilot prépare sa réponse…
      </p>
    </div>
    <button
      v-if="unreadResponse"
      class="secondary conversation-latest"
      type="button"
      @click="showLatest"
    >
      Voir la dernière réponse
    </button>
    <div
      v-if="state.result?.order_status === 'PENDING_CONFIRMATION'"
      class="order-prompt"
    >
      Une commande attend votre décision. Répondez explicitement pour confirmer ou abandonner.
    </div>
    <ApiErrorNotice :error="state.error" />
    <p
      v-if="state.error"
      class="hint"
    >
      <template v-if="state.decisionAttempted || state.result?.intent === 'ORDER'">
        Aucune relance automatique. Une confirmation peut avoir été traitée : demandez le statut avant de recommencer.
      </template>
      <template v-else>
        Aucune relance automatique. Votre historique est conservé ; vérifiez votre connexion avant de réessayer.
      </template>
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
        <small>{{ customerToken ? 'Conversation conservée pendant cette session.' : 'Connectez un JWT client pour commencer.' }}</small>
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

<style scoped>
.catalogue-convention { margin: 12px 24px; }
.conversation-latest { margin: 12px 24px; }
.transcript { overscroll-behavior-y: contain; }
</style>
