<script setup lang="ts">
import { computed } from 'vue'
import { language } from './preferences'
const props = defineProps<{ title: string; values: (number | null)[]; labels: string[]; unit: string }>()
const valid = computed(() => props.values.filter((v): v is number => v !== null))
const top = computed(() => Math.max(1, ...valid.value)), bottom = computed(() => Math.min(0, ...valid.value))
const y = (value: number) => 140 - (value - bottom.value) / (top.value - bottom.value) * 120
const segments = computed(() => {
  const paths: string[] = []; let path = ''
  props.values.forEach((value, i) => {
    if (value === null) { if (path) paths.push(path); path = ''; return }
    path += `${path ? ' L' : 'M'}${20 + i / Math.max(1, props.values.length - 1) * 460},${y(value)}`
  })
  if (path) paths.push(path)
  return paths
})
</script>
<template>
  <figure class="intelligence-chart">
    <figcaption>{{ title }} · {{ unit }}</figcaption>
    <p v-if="!valid.length">
      {{ language === 'fr' ? 'Aucun jour complet pour tous les produits de cette page.' : 'No complete day for all products on this page.' }}
    </p>
    <svg
      v-else
      viewBox="0 0 500 180"
      role="img"
      :aria-label="title + ' : ' + bottom + ' – ' + top + ' ' + unit"
    >
      <line
        x1="20"
        :y1="y(0)"
        x2="480"
        :y2="y(0)"
        stroke="currentColor"
        opacity=".3"
      />
      <path
        v-for="(path, index) in segments"
        :key="index"
        :d="path"
        fill="none"
        stroke="currentColor"
        stroke-width="3"
      />
      <text
        x="20"
        y="14"
      >{{ top }}</text><text
        x="20"
        y="153"
      >{{ bottom }}</text><text
        x="20"
        y="170"
      >{{ labels[0] }}</text><text
        x="480"
        y="170"
        text-anchor="end"
      >{{ labels.at(-1) }}</text>
    </svg>
  </figure>
</template>
<style scoped>
.intelligence-chart { margin:0; min-width:0; color:var(--brand); }
figcaption { color:var(--ink); font-weight:650; margin-bottom:8px; }
svg { width:100%; display:block; overflow:visible; }
text { font:11px system-ui; fill:var(--muted); }
</style>
