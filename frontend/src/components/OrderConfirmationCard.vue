<script setup lang="ts">
defineProps<{ productName: string; quantity: number; disabled: boolean }>()
defineEmits<{ confirm: []; cancel: [] }>()
</script>

<template>
  <section
    class="context-card confirmation-card"
    aria-label="Commande à confirmer"
  >
    <h3>Commande à confirmer</h3>
    <p class="context-product">
      {{ productName }}
    </p>
    <p>Quantité : {{ quantity }}</p>
    <div class="context-actions">
      <button
        type="button"
        :disabled="disabled"
        @click="$emit('confirm')"
      >
        Confirmer
      </button>
      <button
        type="button"
        class="secondary"
        :disabled="disabled"
        @click="$emit('cancel')"
      >
        Annuler
      </button>
    </div>
    <small>Annuler abandonne uniquement cette préparation.</small>
  </section>
</template>
