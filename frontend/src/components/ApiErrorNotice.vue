<script setup lang="ts">
import { ApiError } from '../api/http'
defineProps<{ error: Error | null }>()
</script>

<template>
  <div
    v-if="error"
    class="error-notice"
    role="alert"
  >
    <p>{{ error.message }}</p>
    <small v-if="error instanceof ApiError && error.requestId">
      Reference : {{ error.requestId }}
    </small>
  </div>
</template>
