<script setup lang="ts">
defineProps<{ name: 'dashboard' | 'orders' | 'products' | 'import' }>()
const paths = {
  dashboard: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  orders: 'M7 4h10v3H7z M5 5H3v16h18V5h-2 M7 12h10 M7 16h7',
  products: 'M3 7l9-4 9 4v10l-9 4-9-4z M3 7l9 4 9-4 M12 11v10',
  import: 'M12 16V3 M7 8l5-5 5 5 M3 15v6h18v-6',
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
