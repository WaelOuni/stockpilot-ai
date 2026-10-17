<script setup lang="ts">
import AssistantConversation from '../features/assistant/AssistantConversation.vue'
defineProps<{ initialMessage?: string }>()
</script>

<template>
  <header class="page-heading">
    <span class="eyebrow">Votre copilote outillage</span>
    <h1>Assistant</h1>
    <p>Du conseil à la commande, une conversation à la fois.</p>
  </header>
  <AssistantConversation :initial-message="initialMessage" />
</template>
