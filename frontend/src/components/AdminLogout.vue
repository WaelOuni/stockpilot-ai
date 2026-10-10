<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { adminLogout } from '../api/admin'
import { ApiError } from '../api/http'
import ApiErrorNotice from './ApiErrorNotice.vue'
const router = useRouter()
const loading = ref(false)
const error = ref<Error | null>(null)
async function logout() {
  if (loading.value) return
  loading.value = true
  error.value = null
  try { await adminLogout(); await router.replace('/admin/login') }
  catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) await router.replace('/admin/login')
    else error.value = cause instanceof Error ? cause : new Error('Déconnexion impossible.')
  } finally { loading.value = false }
}
</script>
<template>
  <button
    :disabled="loading"
    @click="logout"
  >
    {{ loading ? 'Déconnexion…' : 'Se déconnecter' }}
  </button>
  <ApiErrorNotice
    v-if="error"
    :error="error"
  />
</template>
