<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { adminLogin } from '../api/admin'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'

const route = useRoute()
const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref<Error | null>(null)
async function login() {
  if (loading.value) return
  loading.value = true
  error.value = null
  const submitted = password.value
  password.value = ''
  try {
    await adminLogin(username.value, submitted)
    await router.replace('/admin/orders')
  } catch (cause) {
    error.value = cause instanceof Error ? cause : new Error('Connexion impossible.')
  } finally { loading.value = false }
}
</script>

<template>
  <header class="page-heading">
    <span class="eyebrow">Accès sécurisé</span>
    <h1>Connexion administrateur</h1>
    <p>Connectez-vous pour gérer les commandes, le catalogue et les stocks.</p>
  </header>
  <p
    v-if="route.query.reason === 'session'"
    role="status"
  >
    Connectez-vous : session absente ou expirée.
  </p>
  <p
    v-if="route.query.reason === 'unavailable'"
    role="alert"
  >
    Vérification de session indisponible.
  </p>
  <ApiErrorNotice
    v-if="error"
    id="form-error"
    :error="error"
  />
  <form
    class="context-card admin-login"
    :aria-describedby="error ? 'form-error' : undefined"
    @submit.prevent="login"
  >
    <label for="admin-username">Identifiant administrateur</label>
    <input
      id="admin-username"
      v-model="username"
      autocomplete="username"
      maxlength="128"
      required
      :disabled="loading"
    />
    <label for="admin-password">Mot de passe administrateur</label>
    <input
      id="admin-password"
      v-model="password"
      type="password"
      autocomplete="current-password"
      minlength="16"
      maxlength="256"
      required
      :disabled="loading"
    />
    <button
      type="submit"
      :disabled="loading"
    >
      {{ loading ? 'Connexion…' : 'Se connecter' }}
    </button>
  </form>
</template>

<style scoped>
.admin-login { max-width: 32rem; display: grid; gap: .6rem; }
.admin-login label { margin: 0; }
.admin-login button { justify-self: start; }
</style>
