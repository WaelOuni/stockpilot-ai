<script setup lang="ts">
import { t } from './preferences'
import { onMounted, onUnmounted, ref } from 'vue'
const props = defineProps<{ title: string; label: string; returnFocus?: HTMLElement | null; busy?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLDialogElement>()
const returnFocus = props.returnFocus ?? document.activeElement
onMounted(() => { panel.value?.showModal() })
onUnmounted(() => {
  panel.value?.close()
  if (returnFocus instanceof HTMLElement && returnFocus.isConnected) returnFocus.focus()
})
</script>
<template>
  <dialog
    ref="panel"
    class="b2b-detail-panel"
    :aria-label="t(label)"
    @cancel.prevent="!busy && emit('close')"
  >
    <header class="b2b-detail-heading">
      <h2>{{ t(title) }}</h2>
      <button
        class="secondary"
        autofocus
        :disabled="busy"
        @click="emit('close')"
      >
        {{ t("Fermer le détail") }}
      </button>
    </header>
    <slot></slot>
  </dialog>
</template>
<style scoped>
.b2b-detail-panel { box-sizing:border-box; width:min(640px,calc(100% - 32px)); max-height:85vh; overflow:auto; padding:24px; border:1px solid #bcc8dc; border-radius:12px; color:#17243b; background:#fff; box-shadow:0 20px 60px #17243b40; }
.b2b-detail-panel::backdrop { background:#17243b66; }
.b2b-detail-heading { display:flex; align-items:start; justify-content:space-between; gap:16px; border-bottom:1px solid #dfe5ef; padding-bottom:12px; margin-bottom:16px; }
.b2b-detail-heading h2 { font-size:1.2rem; margin:0; }
</style>
