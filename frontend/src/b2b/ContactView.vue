<script setup lang="ts">
import SupportContactForm from './SupportContactForm.vue'
import OrganizationContact from './OrganizationContact.vue'
import { t } from './preferences'
</script>
<template>
  <section>
    <h1>{{ t('Aide & contact') }}</h1>
    <p>{{ t('Choisissez le support de l’application ou votre administrateur d’entreprise.') }}</p>
    <div class="contact-columns">
      <SupportContactForm />
      <OrganizationContact />
    </div>
  </section>
</template>
<style scoped>
.contact-columns { display:grid; gap:24px; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); align-items:start; }
@media(max-width:1000px) { .contact-columns { grid-template-columns:minmax(0,1fr); } }
</style>
