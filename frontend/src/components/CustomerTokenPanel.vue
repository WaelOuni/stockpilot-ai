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
    <summary>{{ customerToken ? 'Session client connectée' : 'Connecter un JWT client' }}</summary>
    <p id="customer-token-help">
      Collez un token généré localement. Il reste uniquement en mémoire et disparait au rechargement.
    </p>
    <form
      class="token-form"
      @submit.prevent="connect"
    >
      <label for="customer-token">JWT client</label>
      <div class="input-row">
        <input
          id="customer-token"
          v-model="draft"
          aria-describedby="customer-token-help"
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
          Déconnecter
        </button>
      </div>
    </form>
    <small>La signature et l'expiration sont validées par le backend, pas par ce panneau.</small>
  </details>
</template>
