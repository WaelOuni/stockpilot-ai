<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'
import { computed } from 'vue'
import CustomerTokenPanel from './components/CustomerTokenPanel.vue'
import AppHeader from './components/AppHeader.vue'
import AdminSidebar from './components/AdminSidebar.vue'
const route = useRoute()
const adminArea = computed(() => route.path.toLowerCase().startsWith('/admin/'))
const adminWorkspace = computed(() => adminArea.value && route.path !== '/admin/login')
</script>
<template>
  <a
    class="skip-link"
    href="#main-content"
  >Aller au contenu</a>
  <AppHeader />
  <div
    class="workspace"
    :class="{ 'admin-workspace': adminWorkspace }"
  >
    <AdminSidebar v-if="adminWorkspace" />
    <main
      id="main-content"
      class="main-shell"
      tabindex="-1"
    >
      <CustomerTokenPanel v-if="!adminArea" />
      <RouterView />
    </main>
  </div>
  <footer class="site-footer">
    StockPilot AI · Conseil, commandes et inventaire
  </footer>
</template>
