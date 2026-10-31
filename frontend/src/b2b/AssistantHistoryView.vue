<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useB2B } from './session'
import { conversationList } from './assistant'
import { useTask } from './tasks'
import { StaleResponse } from './client'
import { t, dateLabel } from './preferences'
const session = useB2B()
const { busy, error, run } = useTask()
const items = ref<ReturnType<typeof conversationList>['items']>([])
const offset = ref(0)
let disposed = false
async function load(delta = 0) {
  await run(async () => {
    const next = offset.value + delta * 20
    const result = await session.client.request('/b2b/api/assistant/conversations?limit=20&offset=' + next, conversationList)
    if (disposed) throw new StaleResponse()
    items.value = result.items.slice(0, 20); offset.value = next
  })
}
onMounted(() => { void load() })
onUnmounted(() => { disposed = true })
</script>
<template>
  <section>
    <h1>{{ t('Historique des conversations') }}</h1>
    <RouterLink to="/b2b-app/assistant">
      {{ t('Retour à l’assistant') }}
    </RouterLink>
    <p
      v-if="busy"
      role="status"
    >
      {{ t('Chargement…') }}
    </p>
    <p
      v-if="error"
      role="alert"
    >
      {{ t(error) }}
    </p>
    <button
      v-if="error"
      :disabled="busy"
      @click="load()"
    >
      {{ t('Actualiser') }}
    </button>
    <p v-if="!busy && !error && !items.length">
      {{ t('Aucune conversation sur cette page.') }}
    </p>
    <ul>
      <li
        v-for="item in items"
        :key="item.conversation_id"
      >
        <RouterLink :to="'/b2b-app/assistant/' + encodeURIComponent(item.conversation_id)">
          {{ dateLabel(item.created_at) }} — {{ item.conversation_id }}
        </RouterLink>
      </li>
    </ul>
    <nav
      class="b2b-actions"
      :aria-label="t('Pagination des conversations')"
    >
      <button
        :disabled="busy || offset === 0"
        @click="load(-1)"
      >
        {{ t('Précédent') }}
      </button>
      <span>{{ offset / 20 + 1 }}</span>
      <button
        :disabled="busy || items.length < 20 || offset >= 10000"
        @click="load(1)"
      >
        {{ t('Suivant') }}
      </button>
    </nav>
  </section>
</template>
<style scoped>
li { margin-block: .75rem; overflow-wrap: anywhere; }
</style>
