<script setup lang="ts">
defineProps<{ productName?: string | null; quantity?: number | null }>()
</script>

<template>
  <section
    class="context-card stock-warning"
    aria-label="Stock insuffisant"
  >
    <h3>Stock insuffisant</h3>
    <p
      v-if="productName"
      class="context-product"
    >
      {{ productName }}
    </p>
    <p v-if="quantity !== null && quantity !== undefined">
      Demandé : {{ quantity }}
    </p>
    <small>Consultez la réponse ci-dessus pour le stock disponible.</small>
  </section>
</template>
