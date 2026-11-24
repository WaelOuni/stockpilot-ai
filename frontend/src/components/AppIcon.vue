<script setup lang="ts">
defineProps<{ name: 'dashboard' | 'orders' | 'products' | 'import' | 'eye' | 'archive' | 'restore' | 'cancel' }>()
const paths = {
  dashboard: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  orders: 'M7 4h10v3H7z M5 5H3v16h18V5h-2 M7 12h10 M7 16h7',
  products: 'M3 7l9-4 9 4v10l-9 4-9-4z M3 7l9 4 9-4 M12 11v10',
  import: 'M12 16V3 M7 8l5-5 5 5 M3 15v6h18v-6',
  eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  archive: 'M3 3h18v5H3z M5 8v13h14V8 M10 12h4',
  restore: 'M3 10a9 9 0 1 1 0 5 M3 3v7h7 M12 8v4l3 2',
  cancel: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M6 6l12 12',
}
</script>
<template>
  <svg
    width="20"
    height="20"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  ><path :d="paths[name]" /></svg>
</template>
