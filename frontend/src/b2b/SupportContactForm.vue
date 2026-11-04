<script setup lang="ts">
import { ref } from 'vue'
import { t } from './preferences'
import { supportAddress, supportTypes, supportText, supportMailto } from './contact'
const type = ref<string>(supportTypes[0]), subject = ref(''), description = ref('')
const error = ref(''), notice = ref(''), fallback = ref('')
function prepare() {
  error.value = ''; notice.value = ''
  try {
    const url = supportMailto(type.value, subject.value, description.value)
    notice.value = "Votre application de messagerie va s'ouvrir. Vérifiez puis envoyez votre email."
    const link = document.createElement('a'); link.href = url; link.click()
  } catch (e) { error.value = e instanceof Error ? e.message : 'Action impossible.' }
}
async function copy() {
  error.value = ''; notice.value = ''
  try {
    const value = supportText(type.value, subject.value, description.value)
    const content = supportAddress + '\n' + value.subject + '\n\n' + value.body
    fallback.value = content
    try {
      await navigator.clipboard.writeText(content)
      notice.value = 'Message copié. Collez-le dans votre application de messagerie.'
    } catch { notice.value = 'Copie automatique indisponible. Sélectionnez et copiez le texte ci-dessous.' }
  } catch (e) { error.value = e instanceof Error ? e.message : 'Action impossible.' }
}
</script>
<template>
  <section class="b2b-card support-contact">
    <h2>{{ t('Contacter le support StockPilot') }}</h2>
    <p>{{ supportAddress }}</p>
    <p>{{ t('Ce formulaire prépare un email sur votre appareil. Aucun message support n’est enregistré dans StockPilot.') }}</p>
    <form @submit.prevent="prepare">
      <label>{{ t('Type de demande') }}<select v-model="type"><option
        v-for="value in supportTypes"
        :key="value"
        :value="value"
      >{{ t(value) }}</option></select></label>
      <label>{{ t('Objet obligatoire') }}<input
        v-model="subject"
        required
        maxlength="100"
      /></label>
      <label>{{ t('Description obligatoire') }}<textarea
        v-model="description"
        required
        maxlength="800"
        rows="5"
      ></textarea></label>
      <p class="hint">
        {{ t('Décrivez le problème sans mot de passe, token ni données confidentielles.') }}
      </p>
      <p
        v-if="error"
        role="alert"
      >
        {{ t(error) }}
      </p>
      <p
        v-if="notice"
        role="status"
      >
        {{ t(notice) }}
      </p>
      <div class="b2b-actions">
        <button>{{ t('Préparer mon email') }}</button>
        <button
          type="button"
          class="secondary"
          @click="copy"
        >
          {{ t('Copier le message') }}
        </button>
      </div>
      <label v-if="fallback">{{ t('Texte à copier') }}<textarea
        :value="fallback"
        readonly
        rows="6"
        @focus="($event.target as HTMLTextAreaElement).select()"
      ></textarea></label>
    </form>
  </section>
</template>
<style scoped>
form { display:grid; gap:14px; }
textarea { resize:vertical; }
.support-contact { min-width:0; }
</style>
