<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ category: string }>()
const kind = computed(() => {
  const value = props.category.toLowerCase()
  if (/light|lamp|éclair/.test(value)) return 'light'
  if (/safety|sécur|protect/.test(value)) return 'shield'
  if (/electro|informat|office|bureau/.test(value)) return 'device'
  if (/drill|tool|outill/.test(value)) return 'tool'
  return 'box'
})
</script>
<template>
  <div
    class="product-visual"
    :data-kind="kind"
    aria-hidden="true"
  >
    <svg
      viewBox="0 0 120 80"
      fill="none"
      stroke="currentColor"
      stroke-width="2.5"
      stroke-linejoin="round"
      stroke-linecap="round"
    >
      <ellipse
        cx="60"
        cy="69"
        rx="28"
        ry="3"
        fill="currentColor"
        stroke="none"
        opacity=".1"
      />
      <g v-if="kind === 'light'"><path d="M48 40a18 18 0 1 1 24 0l-3 10H51zM52 56h16M55 62h10M60 8V3M33 20l-5-4M87 20l5-4M30 40h-6M90 40h6" /></g>
      <g v-else-if="kind === 'shield'"><path d="M60 10l26 10v19c0 14-14 23-26 29-12-6-26-15-26-29V20zM47 38l9 9 18-20" /></g>
      <g v-else-if="kind === 'device'"><rect
        x="30"
        y="14"
        width="60"
        height="39"
        rx="4"
      /><path d="M60 53v10M45 64h30M38 23h44M38 31h24" /></g>
      <g v-else-if="kind === 'tool'"><path d="M29 20h43l9 8v16H50l-8 19H29l10-24H29zM81 28h15M96 25v7M50 20v24M58 27h12M58 34h12M28 63h18" /></g>
      <g v-else><path d="M30 26l30-14 30 14v30L60 70 30 56zM30 26l30 15 30-15M60 41v29M45 19l30 15v13M39 47l11 5" /></g>
    </svg>
  </div>
</template>
