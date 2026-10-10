import { reactive, readonly, watch } from 'vue'
import { sendAssistantMessage } from '../../api/client'
import { customerToken } from '../../utils/customerSession'
import type { AssistantResponse } from '../../types/api'

export interface ConversationMessage {
  id: string
  role: 'customer' | 'assistant'
  text: string
  result?: AssistantResponse
}

const state = reactive({
  threadId: crypto.randomUUID(),
  messages: [] as ConversationMessage[],
  result: null as AssistantResponse | null,
  loading: false,
  error: null as Error | null,
  decisionAttempted: false,
})
let controller: AbortController | null = null

function reset() {
  controller?.abort()
  controller = null
  state.threadId = crypto.randomUUID()
  state.messages = []
  state.result = null
  state.loading = false
  state.error = null
  state.decisionAttempted = false
}

watch(customerToken, reset, { flush: 'sync' })

async function send(message: string): Promise<void> {
  if (!message.trim() || state.loading || !customerToken.value) return
  const request = new AbortController()
  controller = request
  state.loading = true
  state.error = null
  state.messages.push({ id: crypto.randomUUID(), role: 'customer', text: message.trim() })
  try {
    const result = await sendAssistantMessage(
      { message: message.trim(), thread_id: state.threadId }, request.signal,
    )
    if (request.signal.aborted) return
    state.result = result
    state.decisionAttempted = false
    state.messages.push({ id: crypto.randomUUID(), role: 'assistant', text: result.response, result })
  } catch (error) {
    if (!request.signal.aborted) state.error = error instanceof Error ? error : new Error('Erreur inattendue.')
  } finally {
    if (controller === request) {
      controller = null
      state.loading = false
    }
  }
}

async function decide(decision: 'confirm' | 'cancel') {
  if (state.loading || state.error || state.decisionAttempted ||
      state.result?.intent !== 'ORDER' || state.result.order_status !== 'PENDING_CONFIRMATION') return
  state.decisionAttempted = true
  await send(decision === 'confirm' ? 'Oui je confirme' : 'Non annule')
}

export function useAssistant() {
  return { state: readonly(state), send, reset, decide }
}
