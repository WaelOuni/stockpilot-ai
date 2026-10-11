<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
const route = useRoute()
const open = ref(false)
const toggle = ref<HTMLButtonElement | null>(null)
watch(() => route.fullPath, () => { open.value = false })
function close() { open.value = false; toggle.value?.focus() }
</script>
<template>
  <header
    class="site-header"
    @keydown.esc="close"
  >
    <div class="header-inner">
      <RouterLink
        class="brand"
        to="/products"
      >
        <span
          class="brand-mark"
          aria-hidden="true"
        >S</span>StockPilot <span class="brand-ai">AI</span>
      </RouterLink>
      <button
        ref="toggle"
        class="menu-toggle secondary"
        aria-controls="primary-navigation"
        :aria-expanded="open"
        @click="open = !open"
      >
        {{ open ? 'Fermer le menu' : 'Menu' }}
      </button>
      <nav
        id="primary-navigation"
        aria-label="Navigation principale"
        :class="{ open }"
      >
        <RouterLink to="/products">
          Catalogue
        </RouterLink>
        <RouterLink to="/assistant">
          Assistant
        </RouterLink>
        <RouterLink to="/orders">
          Mes commandes
        </RouterLink>
        <RouterLink to="/admin/orders">
          Admin
        </RouterLink>
      </nav>
      <span class="demo-badge">Espace local</span>
    </div>
  </header>
</template>
