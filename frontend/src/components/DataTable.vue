<script setup lang="ts">
defineProps<{ label: string }>()
</script>
<template>
  <div
    class="table-scroll"
    role="region"
    :aria-label="label"
    tabindex="0"
  >
    <table class="data-table">
      <caption class="sr-only">
        {{ label }}
      </caption><slot></slot>
    </table>
  </div>
</template>
