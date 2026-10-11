<script setup lang="ts">
import CustomerOrders from '../features/orders/CustomerOrders.vue'
</script>

<template>
  <header class="page-heading">
    <span class="eyebrow">Votre suivi personnel</span>
    <h1>Mes commandes</h1>
    <p>Retrouvez vos commandes et leur dernier statut enregistré.</p>
  </header>
  <CustomerOrders />
</template>
