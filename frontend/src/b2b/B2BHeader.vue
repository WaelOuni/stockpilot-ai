<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { language, theme, t } from './preferences'
import { useB2B } from './session'

defineProps<{ busy: boolean }>()
defineEmits<{ logout: []; switchAccount: [] }>()
const session = useB2B()
const { current } = session
const route = useRoute()
const account = ref<HTMLDetailsElement>()
const trigger = ref<HTMLElement>()
const roleNames = { READ_ONLY: 'Consultation', USER: 'Utilisateur', MANAGER: 'Responsable', ORGANIZATION_ADMIN: 'Administrateur entreprise' }
function close(returnFocus = false) {
  if (!account.value?.open) return
  account.value.open = false
  if (returnFocus) trigger.value?.focus()
}
function outside(event: MouseEvent) { if (event.target instanceof Node && !account.value?.contains(event.target)) close() }
watch(() => route.path, () => close())
watch(current, () => close())
onMounted(() => document.addEventListener('click', outside))
onUnmounted(() => document.removeEventListener('click', outside))
</script>
<template>
  <header class="b2b-header workspace-header">
    <RouterLink
      class="b2b-brand"
      to="/b2b-app/"
    >
      <strong>StockPilot AI</strong><span>{{ t('Espace entreprise') }}</span>
    </RouterLink>
    <nav
      v-if="current"
      class="workspace-nav"
      :aria-label="t('Navigation entreprise')"
    >
      <RouterLink
        v-if="current.assistant_available && session.has('conversation:own')"
        to="/b2b-app/assistant"
      >
        {{ t('Assistant') }}
      </RouterLink>
      <RouterLink
        v-if="session.has('catalogue:read')"
        to="/b2b-app/"
      >
        {{ t('Catalogue') }}
      </RouterLink>
      <RouterLink
        v-if="session.has('order:read_own') || session.has('order:read_organization')"
        to="/b2b-app/orders"
      >
        {{ t('Commandes') }}
      </RouterLink>
      <RouterLink
        v-if="session.has('stock:read')"
        to="/b2b-app/stock"
      >
        {{ t('Administration') }}
      </RouterLink>
    </nav>
    <div
      v-if="current"
      class="workspace-organization"
    >
      <span class="hint">{{ t('Organisation active') }}</span><strong>{{ current.organization_name }}</strong>
    </div>
    <details
      ref="account"
      class="workspace-account"
      @keydown.esc.stop.prevent="close(true)"
    >
      <summary
        ref="trigger"
        :aria-label="t('Compte et préférences')"
      >
        <span
          class="account-avatar"
          aria-hidden="true"
        >{{ current ? current.user_id.slice(0, 1).toUpperCase() : '○' }}</span>
        <span class="account-label">{{ current?.user_id ?? t('Préférences') }}</span><svg
          class="account-chevron"
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        ><path d="m6 9 6 6 6-6" /></svg>
      </summary>
      <div class="workspace-account-panel">
        <div
          v-if="current"
          class="workspace-account-identity"
        >
          <strong>{{ current.user_id }}</strong><span>{{ t(roleNames[current.role]) }}</span>
          <span>{{ current.organization_name }}</span>
        </div>
        <div class="workspace-preferences">
          <label>{{ t('Langue') }}<select
            v-model="language"
            :aria-label="t('Langue')"
          ><option value="fr">Français</option><option value="en">English</option></select></label>
          <button
            class="secondary"
            :aria-pressed="theme === 'dark'"
            @click="theme = theme === 'dark' ? 'light' : 'dark'"
          >
            {{ theme === 'dark' ? t('Mode clair') : t('Mode sombre') }}
          </button>
        </div>
        <template v-if="current">
          <button
            class="secondary"
            :disabled="busy"
            @click="close(); $emit('switchAccount')"
          >
            {{ t('Changer de compte') }}
          </button>
          <button
            class="secondary"
            :disabled="busy"
            @click="close(); $emit('logout')"
          >
            {{ t('Se déconnecter') }}
          </button>
        </template>
      </div>
    </details>
  </header>
</template>
<style>
.b2b-shell .workspace-header { display:flex; gap:24px; flex-wrap:nowrap; align-items:center; padding:12px max(20px,calc((100vw - 1360px)/2)); }
.workspace-header .b2b-brand { display:grid; text-decoration:none; color:inherit; align-self:center; flex-shrink:0; gap:2px; }
.workspace-header .b2b-brand strong { font-size:1.05rem; color:var(--brand); }
.workspace-header .b2b-brand span { color:var(--muted); font-size:.75rem; }
.workspace-nav { display:flex; align-items:center; gap:4px; flex:1; min-width:0; }
.workspace-nav a { flex-shrink:0; display:flex; align-items:center; min-height:44px; padding:10px 12px; color:var(--muted); font-weight:600; text-decoration:none; border-radius:8px; }
.workspace-nav a:hover { background:var(--soft); color:var(--ink); }
.workspace-nav a.router-link-active:not([href='/b2b-app/']), .workspace-nav a.router-link-exact-active { background:var(--soft); color:var(--brand); box-shadow:inset 0 -3px var(--brand); }
.b2b-shell .workspace-header .workspace-organization { display:grid; gap:2px; text-align:left; max-width:210px; min-width:0; }
.workspace-organization .hint { font-size:.7rem; }
.workspace-organization strong { font-size:.85rem; overflow-wrap:anywhere; }
.workspace-account { position:relative; min-width:0; }
.workspace-account summary { list-style:none; display:flex; align-items:center; gap:8px; padding:4px 8px; min-height:44px; cursor:pointer; border:1px solid var(--line); border-radius:10px; }
.workspace-account summary::-webkit-details-marker { display:none; }
.account-chevron { width:18px; height:18px; flex-shrink:0; transition:transform .15s; }
.workspace-account[open] .account-chevron { transform:rotate(180deg); }
.workspace-account summary:hover, .workspace-account[open] summary { background:var(--soft); }
.workspace-account summary:focus-visible { outline:3px solid var(--brand); outline-offset:3px; }
.account-avatar { display:grid; place-items:center; width:28px; height:28px; flex-shrink:0; border-radius:50%; background:var(--soft); color:var(--brand); font-weight:700; }
.account-label { max-width:120px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:.8rem; }
.workspace-account-panel { position:absolute; right:0; top:calc(100% + 8px); z-index:30; display:grid; gap:12px; width:300px; max-width:calc(100vw - 32px); padding:18px; border:1px solid var(--line); border-radius:12px; box-shadow:0 12px 36px #10172222; background:var(--surface); color:var(--ink); }
.workspace-account-identity { display:grid; gap:4px; padding-bottom:12px; border-bottom:1px solid var(--line); overflow-wrap:anywhere; font-size:.875rem; }
.workspace-account-identity span { color:var(--muted); }
.workspace-preferences { display:grid; gap:12px; }
.workspace-preferences label { font-size:.8rem; color:var(--muted); }
.workspace-preferences select { margin-top:4px; }
.workspace-account-panel button { min-height:44px; width:100%; }
@media(max-width:1100px) {
  .b2b-shell .workspace-header { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px 16px; padding:12px 16px 0; }
  .workspace-header .b2b-brand { grid-column:1; grid-row:1; }
  .workspace-header .workspace-account { grid-column:2; grid-row:1 / span 2; align-self:start; }
  .b2b-shell .workspace-header .workspace-organization { grid-column:1; grid-row:2; width:auto; max-width:none; }
  .workspace-organization .hint { display:none; }
  .workspace-nav { grid-column:1 / -1; grid-row:3; overflow-x:auto; padding:3px 3px 7px; scrollbar-width:thin; }
  .workspace-nav a { font-size:.875rem; padding:10px; }
  .workspace-account .account-label { max-width:82px; }
}
</style>
