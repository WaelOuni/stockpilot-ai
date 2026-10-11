<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { RouterLink } from 'vue-router'
</script>
<template>
  <aside class="admin-sidebar">
    <p class="eyebrow">
      Espace administrateur
    </p>
    <nav aria-label="Navigation back-office">
      <RouterLink to="/admin/dashboard">
        <AppIcon name="dashboard" /> Tableau de bord
      </RouterLink>
      <RouterLink to="/admin/orders">
        <AppIcon name="orders" /> Commandes admin
      </RouterLink>
      <RouterLink to="/admin/products">
        <AppIcon name="products" /> Produits admin
      </RouterLink>
      <RouterLink to="/admin/products/import">
        <AppIcon name="import" /> Import CSV
      </RouterLink>
    </nav>
    <p class="hint">
      Catalogue, commandes et inventaire dans un même espace.
    </p>
  </aside>
</template>
