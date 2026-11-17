<script setup lang="ts">
import { ref, useId } from 'vue'
import AppIcon from '../components/AppIcon.vue'
defineProps<{ icon: 'eye' | 'archive' | 'restore' | 'cancel'; label: string; tooltip: string; disabled?: boolean; danger?: boolean }>()
defineEmits<{ click: [event: MouseEvent] }>()
const id = useId(), visible = ref(false)
</script>
<template>
  <span
    class="row-action"
    @mouseenter="visible = true"
    @mouseleave="visible = false"
    @keydown.esc.stop="visible = false"
  >
    <button
      type="button"
      class="secondary row-action-button"
      :class="{ 'danger-link': danger }"
      :aria-label="label"
      :aria-describedby="visible ? id : undefined"
      :disabled="disabled"
      @focus="visible = true"
      @blur="visible = false"
      @click="$emit('click', $event)"
    >
      <AppIcon :name="icon" />
      <span class="row-action-text">{{ label }}</span>
    </button>
    <span
      v-show="visible"
      :id="id"
      class="row-action-tooltip"
      role="tooltip"
    >{{ tooltip }}</span>
  </span>
</template>
