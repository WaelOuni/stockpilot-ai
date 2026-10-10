<script setup lang="ts">
import { ref } from 'vue'
import { customerToken, setCustomerToken } from '../utils/customerSession'

const draft = ref('')
function connect() {
  setCustomerToken(draft.value)
  draft.value = ''
}
function disconnect() {
  setCustomerToken('')
  draft.value = ''
}
</script>

<template>
  <details
    class="token-panel"
    :open="!customerToken"
  >
    <summary>{{ customerToken ? 'Session demo connectee' : 'Connecter un JWT client' }}</summary>
    <p>Collez un token genere localement. Il reste uniquement en memoire et disparait au rechargement.</p>
    <form
      class="token-form"
      @submit.prevent="connect"
    >
      <label for="customer-token">JWT client</label>
      <div class="input-row">
        <input
          id="customer-token"
          v-model="draft"
          type="password"
          autocomplete="off"
          spellcheck="false"
          placeholder="Collez votre JWT ici"
        />
        <button
          type="submit"
          :disabled="!draft.trim()"
        >
          Connecter
        </button>
        <button
          v-if="customerToken"
          type="button"
          class="secondary"
          @click="disconnect"
        >
          Deconnecter
        </button>
      </div>
    </form>
    <small>La signature et l'expiration sont validees par le backend, pas par ce panneau.</small>
  </details>
</template>
