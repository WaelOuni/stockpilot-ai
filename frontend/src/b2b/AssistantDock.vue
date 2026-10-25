<script setup lang="ts">
import { t } from './preferences'
import { ref, onUnmounted } from 'vue'
import AssistantView from './AssistantView.vue'
const panel = ref<HTMLDialogElement>()
const opened = ref(false)
const initialized = ref(false)
const trigger = ref<HTMLButtonElement>()
function open() { initialized.value = true; opened.value = true; panel.value?.showModal() }
function close() { opened.value = false; panel.value?.close(); trigger.value?.focus() }
onUnmounted(() => panel.value?.close())
</script>
<template>
  <button
    ref="trigger"
    class="assistant-launcher"
    :aria-expanded="opened"
    aria-controls="assistant-dock"
    @click="open"
  >
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      aria-hidden="true"
    ><path d="M5 5h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-8l-6 4v-4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z" /><path d="m12 7 1.5 3.5L17 12l-3.5 1.5L12 17l-1.5-3.5L7 12l3.5-1.5Z" /></svg>
    {{ t("Assistant IA") }}
  </button>
  <dialog
    id="assistant-dock"
    ref="panel"
    class="b2b-detail-panel assistant-dock"
    :aria-label="t('Assistant IA')"
    @cancel.prevent="close"
  >
    <header class="b2b-detail-heading">
      <strong>{{ t("Votre assistant") }}</strong><button
        class="secondary"
        autofocus
        @click="close"
      >
        {{ t("Réduire l’assistant") }}
      </button>
    </header>
    <AssistantView
      v-if="initialized"
      embedded
    />
  </dialog>
</template>
