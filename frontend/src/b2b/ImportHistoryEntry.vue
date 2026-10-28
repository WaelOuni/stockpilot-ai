<script setup lang="ts">
import type { Preview } from './contracts'
import { t, dateLabel } from './preferences'

defineProps<{ entry: Preview }>()
const modes: Record<Preview['mode'], string> = {
  stock_only: 'Stock physique uniquement',
  metadata_only: 'Descriptions et prix uniquement',
  create_only: 'Nouveaux produits uniquement',
}
const statuses: Record<Preview['status'], string> = {
  preview: 'Aperçu enregistré — non appliqué',
  invalid: 'Fichier invalide',
  applied: 'Import appliqué',
  rejected: 'Import refusé',
}
</script>

<template>
  <li class="import-history-entry">
    <p><strong>{{ t(modes[entry.mode]) }}</strong></p>
    <p>{{ t(statuses[entry.status]) }} · {{ entry.rows }} {{ t('ligne(s)') }}</p>
    <details>
      <summary>{{ t('Références et détails') }}</summary>
      <dl>
        <dt>{{ t('Identifiant de l’import') }}</dt>
        <dd><code>{{ entry.import_id }}</code></dd>
        <dt>{{ t('Empreinte du fichier') }}</dt>
        <dd><code>{{ entry.file_fingerprint }}</code></dd>
        <dt>{{ t('Codes de diagnostic') }}</dt>
        <dd><code>{{ entry.mode }} · {{ entry.status }}</code></dd>
        <dt>{{ t('Échéance de l’aperçu') }}</dt>
        <dd>{{ dateLabel(entry.expires_at) }}</dd>
      </dl>
    </details>
  </li>
</template>

<style scoped>
.import-history-entry { padding:16px 0; border-bottom:1px solid var(--line); }
.import-history-entry p { margin:0 0 8px; }
.import-history-entry summary { cursor:pointer; color:var(--brand); width:fit-content; }
.import-history-entry dl { margin:12px 0 0; }
.import-history-entry dt { color:var(--muted); margin-top:12px; }
.import-history-entry dd { margin:4px 0 0; overflow-wrap:anywhere; text-align:left; }
</style>
