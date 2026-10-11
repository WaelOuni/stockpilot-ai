<script setup lang="ts">
import { productCategoryLabels } from '../utils/products'
const search = defineModel<string>('search', { default: '' })
const category = defineModel<string>('category', { default: '' })
const availability = defineModel<string>('availability', { default: '' })
defineProps<{ loading?: boolean }>()
const emit = defineEmits<{ apply: []; reset: [] }>()
</script>
<template>
  <form
    class="catalogue-filters"
    @submit.prevent="emit('apply')"
  >
    <label>Recherche<input
      v-model="search"
      type="search"
      maxlength="100"
      placeholder="Nom ou SKU"
    /></label>
    <label>Catégorie<select
      v-model="category"
      aria-label="Catégorie"
    ><option value="">Toutes</option><option
      v-for="(label, value) in productCategoryLabels"
      :key="value"
      :value="value"
    >{{ label }}</option></select></label>
    <label>Disponibilité<select
      v-model="availability"
      aria-label="Disponibilité"
    ><option value="">Tous</option><option value="true">Disponibles</option><option value="false">Indisponibles</option></select></label>
    <button
      type="submit"
      :disabled="loading"
    >
      Rechercher
    </button>
    <button
      type="button"
      class="secondary"
      @click="emit('reset')"
    >
      Réinitialiser les filtres
    </button>
  </form>
</template>
