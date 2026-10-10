<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
import CustomerTokenPanel from './components/CustomerTokenPanel.vue'
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
        <RouterLink to="/assistant">
          Assistant
        </RouterLink>
        <RouterLink to="/orders">
          My Orders
        </RouterLink>
      </nav>
      <span class="demo-badge">Local demo</span>
    </div>
  </header>
  <main
    id="main-content"
    class="main-shell"
  >
    <CustomerTokenPanel />
    <RouterView />
  </main>
  <footer class="site-footer">
    StockPilot AI · Conseil et commandes · Demonstration locale
  </footer>
</template>
