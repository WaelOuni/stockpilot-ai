<script setup lang="ts">
import { onMounted, onUnmounted, provide, ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { createB2BSession, sessionKey } from './session'
import { useTask } from './tasks'
import OperationPanel from './OperationPanel.vue'
const session = createB2BSession()
provide(sessionKey, session)
const { current, phase, notice, ssoLogout, expiresSoon } = session
const organization = ref('')
const roleNames = { READ_ONLY: 'Consultation', USER: 'Utilisateur', MANAGER: 'Responsable', ORGANIZATION_ADMIN: 'Administrateur entreprise' }
const { busy, error, run } = useTask()
onMounted(() => { void session.start() })
onUnmounted(session.dispose)
async function login() { await run(async () => { location.assign(await session.login(organization.value)) }) }
</script>
<template>
  <div class="b2b-shell">
    <a
      class="skip-link"
      href="#b2b-content"
    >Aller au contenu</a>
    <header class="b2b-header">
      <RouterLink
        class="b2b-brand"
        to="/b2b-app/"
      >
        <span class="eyebrow">StockPilot AI</span><strong>Espace entreprise</strong>
      </RouterLink>
      <div v-if="current">
        <strong>{{ current.organization_name }}</strong>
        <p class="hint">
          Utilisateur {{ current.user_id }} · {{ roleNames[current.role] }}
        </p>
        <button
          class="secondary"
          :disabled="busy"
          @click="run(session.logout)"
        >
          Se déconnecter
        </button>
      </div>
    </header>
    <nav
      v-if="current"
      class="b2b-nav"
      aria-label="Navigation entreprise"
    >
      <RouterLink
        v-if="session.has('catalogue:read')"
        to="/b2b-app/"
      >
        Catalogue
      </RouterLink>
      <RouterLink
        v-if="session.has('order:read_own') || session.has('order:read_organization')"
        to="/b2b-app/orders"
      >
        Commandes
      </RouterLink>
      <RouterLink
        v-if="session.has('stock:read')"
        to="/b2b-app/stock"
      >
        Stock et administration
      </RouterLink>
    </nav>
    <main
      id="b2b-content"
      class="b2b-content"
      tabindex="-1"
    >
      <p
        v-if="notice"
        class="b2b-notice"
        role="status"
      >
        {{ notice }}
      </p>
      <p
        v-if="error"
        role="alert"
      >
        {{ error }}
      </p>
      <p
        v-if="phase === 'loading'"
        role="status"
      >
        Vérification de votre session…
      </p>
      <section
        v-else-if="phase !== 'active'"
        class="b2b-card b2b-login"
      >
        <p class="eyebrow">
          Bienvenue dans votre espace
        </p>
        <h1>Votre catalogue. Vos commandes. Votre stock.</h1>
        <p>Connectez-vous avec votre compte professionnel Keycloak.</p>
        <form @submit.prevent="login">
          <label>Code d’entreprise
            <input
              v-model="organization"
              autocomplete="organization"
              required
              maxlength="128"
              :disabled="busy"
            />
          </label>
          <p class="hint">
            Utilisez le code communiqué par votre administrateur. Le serveur vérifie votre appartenance ; ce code n’accorde aucun droit.
          </p>
          <button :disabled="busy || !organization.trim()">
            Continuer avec Keycloak
          </button>
        </form>
        <button
          class="secondary"
          :disabled="busy"
          @click="run(session.restore)"
        >
          Vérifier la session existante
        </button>
        <a
          v-if="ssoLogout"
          :href="ssoLogout"
          referrerpolicy="no-referrer"
        >Fermer aussi la session Keycloak</a>
      </section>
      <template v-else-if="current">
        <section
          v-if="expiresSoon"
          class="b2b-notice"
          role="status"
        >
          <p>La session arrive à expiration. Aucun brouillon n’est une commande confirmée.</p>
          <button
            class="secondary"
            :disabled="busy"
            @click="run(session.restore)"
          >
            Revérifier la session
          </button>
        </section>
        <OperationPanel />
        <RouterView :key="current.user_id + ':' + current.organization_id + ':' + current.csrf_token + ':' + session.businessRevision.value" />
      </template>
    </main>
    <footer class="b2b-footer">
      Espace B2B de test · Organisation vérifiée côté serveur · Assistant IA prévu en V13.8.3
    </footer>
  </div>
</template>
<style>
.b2b-shell { min-height:100vh; background:#f5f7fb; color:#17243b; }
.b2b-header { display:flex; justify-content:space-between; gap:24px; padding:24px max(24px,calc((100vw - 1200px)/2)); background:#fff; border-bottom:1px solid #e1e6ef; }
.b2b-brand { display:grid; gap:4px; text-decoration:none; color:inherit; align-self:center; }
.b2b-brand strong { font-size:1.45rem; }
.b2b-header p { margin:4px 0 12px; }
.b2b-nav { display:flex; flex-wrap:wrap; gap:12px; max-width:1200px; margin:auto; padding:18px 24px 0; }
.b2b-nav a { padding:12px 16px; border-radius:8px; color:#334155; text-decoration:none; font-weight:600; }
.b2b-nav a.router-link-exact-active { background:#4338ca; color:white; }
.b2b-content { max-width:1200px; margin:auto; padding:24px; }
.b2b-card { overflow-wrap:anywhere; background:white; border:1px solid #dfe5ef; border-radius:12px; padding:24px; margin-bottom:20px; }
.b2b-login { max-width:640px; margin:32px auto; }
.b2b-login form { display:grid; gap:16px; margin:24px 0; }
.b2b-login > button, .b2b-login > a { margin:8px 12px 8px 0; display:inline-block; }
.b2b-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:16px; }
.b2b-actions { display:flex; flex-wrap:wrap; align-items:end; gap:12px; margin:16px 0; }
.b2b-actions label { flex:1; min-width:140px; }
.b2b-notice { background:#eef2ff; border-left:4px solid #4338ca; border-radius:6px; padding:16px; margin:16px 0; overflow-wrap:anywhere; }
.b2b-notice h2 { font-size:1.1rem; margin-top:0; }
.b2b-notice li { margin:12px 0; display:flex; flex-wrap:wrap; gap:12px; align-items:center; }
.b2b-table { overflow-x:auto; }
.b2b-table table { width:100%; min-width:640px; border-collapse:collapse; }
.b2b-table th,.b2b-table td { padding:12px; text-align:left; border-bottom:1px solid #e1e6ef; vertical-align:top; }
.b2b-status { font-weight:700; color:#3730a3; }
.b2b-footer { text-align:center; padding:28px; color:#526174; font-size:.875rem; }
.b2b-card [role=alert] { color:#9f1239; }
.b2b-card h2 { font-size:1.25rem; }
.b2b-card .b2b-price { font-size:1.4rem; font-weight:700; }
@media(max-width:600px) { .b2b-header { padding:20px; flex-direction:column; } .b2b-content { padding:16px; } .b2b-card { padding:18px; } }
</style>
