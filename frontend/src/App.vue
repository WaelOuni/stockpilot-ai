<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { computed } from 'vue'
import CustomerTokenPanel from './components/CustomerTokenPanel.vue'
const route = useRoute()
const adminArea = computed(() => route.path.toLowerCase().startsWith('/admin/'))
</script>

<template>
  <a
    class="skip-link"
    href="#main-content"
  >Aller au contenu</a>
  <header class="site-header">
    <div class="header-inner">
      <RouterLink
        class="brand"
        to="/assistant"
      >
        <span
          class="brand-mark"
          aria-hidden="true"
        >S</span>StockPilot <span class="brand-ai">AI</span>
      </RouterLink>
      <nav aria-label="Navigation principale">
        <RouterLink to="/products">
          Catalogue
        </RouterLink>
        <RouterLink to="/assistant">
          Assistant
        </RouterLink>
        <RouterLink to="/orders">
          My Orders
        </RouterLink>
        <RouterLink to="/admin/orders">
          Admin
        </RouterLink>
      </nav>
      <nav
        v-if="adminArea"
        aria-label="Navigation back-office"
      >
        <RouterLink to="/admin/orders">
          Commandes admin
        </RouterLink>
        <RouterLink to="/admin/products">
          Produits admin
        </RouterLink>
      </nav>
      <span class="demo-badge">Local demo</span>
    </div>
  </header>
  <main
    id="main-content"
    class="main-shell"
  >
    <CustomerTokenPanel v-if="!adminArea" />
    <RouterView />
  </main>
  <footer class="site-footer">
    StockPilot AI · Conseil et commandes · Demonstration locale
  </footer>
</template>
