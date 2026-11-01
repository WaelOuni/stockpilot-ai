<script setup lang="ts">
import { ref, watch } from 'vue'
import { t } from './preferences'
export type Suggestion = { value: string; label: string; detail?: string }
const props = defineProps<{ id: string; label: string; modelValue: string; options: Suggestion[]; disabled?: boolean; loading?: boolean; hint?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; select: [value: string] }>()
const open = ref(false)
const active = ref(-1)
watch(() => props.options, () => { active.value = -1 })
watch(() => props.disabled, () => { open.value = false })
function choose(index: number) {
  const option = props.options[index]
  if (!option) return
  emit('update:modelValue', option.value); emit('select', option.value)
  open.value = false; active.value = -1
}
function key(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); open.value = false; active.value = -1 }
  else if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault(); open.value = true
    const count = props.options.length
    active.value = count ? (active.value + (event.key === 'ArrowDown' ? 1 : -1) + count) % count : -1
  } else if (event.key === 'Enter' && open.value && active.value >= 0) {
    event.preventDefault(); choose(active.value)
  } else if (event.key === 'Tab') open.value = false
}
</script>
<template>
  <div class="catalogue-combobox">
    <label :for="id">{{ label }}</label>
    <input
      :id="id"
      :value="modelValue"
      :disabled="disabled"
      maxlength="128"
      autocomplete="off"
      role="combobox"
      aria-autocomplete="list"
      :aria-expanded="open"
      :aria-controls="id + '-options'"
      :aria-activedescendant="open && active >= 0 ? id + '-option-' + active : undefined"
      :aria-describedby="hint ? id + '-hint' : undefined"
      @input="emit('update:modelValue', ($event.target as HTMLInputElement).value); open = true; active = -1"
      @focus="open = true"
      @blur="open = false"
      @keydown="key"
    />
    <ul
      v-show="open"
      :id="id + '-options'"
      role="listbox"
      :aria-label="label"
      class="catalogue-options"
    >
      <li
        v-for="(option, index) in options"
        :id="id + '-option-' + index"
        :key="option.value"
        role="option"
        :aria-selected="index === active"
        @mousedown.prevent
        @click="choose(index)"
      >
        <strong>{{ option.label }}</strong><span v-if="option.detail">{{ option.detail }}</span>
      </li>
      <li
        v-if="loading"
        role="presentation"
      >
        {{ t('Chargement…') }}
      </li>
      <li
        v-else-if="!options.length"
        role="presentation"
      >
        {{ t('Aucune suggestion. La saisie libre reste possible.') }}
      </li>
    </ul>
    <small
      v-if="hint"
      :id="id + '-hint'"
      class="hint"
    >{{ hint }}</small>
  </div>
</template>
<style scoped>
.catalogue-combobox { position:relative; flex:1 1 220px; min-width:0; }
.catalogue-combobox label { display:block; }
.catalogue-options { position:absolute; top:100%; left:0; right:0; z-index:20; margin:0; padding:4px; max-height:260px; overflow:auto; list-style:none; border:1px solid var(--line); border-radius:8px; background:var(--surface); color:var(--ink); box-shadow:0 8px 24px #10172222; }
.catalogue-options li { display:grid; gap:4px; padding:10px; overflow-wrap:anywhere; cursor:pointer; }
.catalogue-options li[aria-selected=true], .catalogue-options li:hover { background:var(--soft); color:var(--brand); }
.catalogue-options span { font-size:.85rem; }
</style>
