<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useB2B } from './session'
import { language, dateLabel } from './preferences'
import { array, object, productDecoder, text } from './contracts'
import { StaleResponse } from './client'
import { analyticMoney, intelligenceDecoder, pageABC, type IntelligenceData } from './intelligence'
import IntelligenceChart from './IntelligenceChart.vue'
import CatalogueCombobox from './CatalogueCombobox.vue'
import DetailsPanel from './DetailsPanel.vue'

const session = useB2B()
const i = (fr: string, en: string) => language.value === 'fr' ? fr : en
const allowed = computed(() => session.has('analytics:read') && ['MANAGER', 'ORGANIZATION_ADMIN'].includes(session.current.value?.role ?? ''))
const data = ref<IntelligenceData | null>(null), busy = ref(false), error = ref(''), detailId = ref<string | null>(null)
const period = ref(90), horizon = ref(7), category = ref(''), status = ref('active'), threshold = ref(5), discount = ref(10), offset = ref(0)
const sku = ref(''), productId = ref(''), categories = ref<string[]>([]), suggestions = ref<ReturnType<typeof productDecoder>[]>([])
const chosen = computed(() => data.value?.items.find(item => item.product_id === detailId.value) ?? null)
const abc = computed(() => data.value ? pageABC(data.value.items) : null)
const priorities = ['administrative_movements_to_review', 'no_observed_sales_in_period', 'low_stock_threshold', 'out_of_stock', 'replenishment_estimate']
const priority = (reasons: string[]) => Math.max(-1, ...reasons.map(reason => priorities.indexOf(reason)))
const recommendations = computed(() => data.value?.items.filter(item => priority(item.reasons) >= 0).sort((a, b) => priority(b.reasons) - priority(a.reasons) || a.sku.localeCompare(b.sku)) ?? [])
const bestSales = computed(() => data.value?.items.filter(item => item.period_complete && item.period_sales_units !== null).sort((a, b) => b.period_sales_units! - a.period_sales_units! || a.sku.localeCompare(b.sku)).slice(0, 3) ?? [])
const synthetic = computed(() => data.value?.items.some(item => item.dataset === 'DEMO_SYNTHETIC'))
const missing = () => i('Non disponible', 'Unavailable')
const number = (value: number | string | null | undefined) => value == null ? missing() : new Intl.NumberFormat(language.value === 'fr' ? 'fr-FR' : 'en-GB', { maximumFractionDigits: 2 }).format(typeof value === 'string' ? BigInt(value) : value)
const forecastLabel = (value: string) => ({ estimated: i('Estimation mesurée', 'Measured estimate'), no_commercial_history: i('Aucune vente qualifiée', 'No qualified sales history'), insufficient_history: i('Historique trop court', 'Insufficient history'), incomplete_history: i('Historique incomplet', 'Incomplete history'), mixed_dataset: i('Sources DEMO et réelles mélangées', 'Mixed DEMO and observed sources'), stockout_censored: i('Demande masquée par une rupture', 'Demand censored by stockouts') }[value] ?? missing())
const reasonLabel = (value: string) => ({ out_of_stock: i('À surveiller · rupture actuelle', 'Watch · currently out of stock'), low_stock_threshold: i('À surveiller · seuil de stock faible', 'Watch · low-stock threshold'), replenishment_estimate: i('À réapprovisionner · besoin estimé', 'Replenish · estimated need'), no_observed_sales_in_period: i('Opportunité · aucune vente observée', 'Opportunity · no observed sales'), administrative_movements_to_review: i('À analyser · ajustements administratifs', 'Review · administrative adjustments') }[value] ?? forecastLabel(value))
let active: AbortController | null = null, suggestionVersion = 0
async function load(reset = false) {
  if (!allowed.value) return
  if (reset) offset.value = 0
  active?.abort(); const controller = new AbortController(); active = controller
  busy.value = true; error.value = ''; data.value = null; detailId.value = null
  try {
    if (sku.value && !productId.value) throw new Error(i('Sélectionnez un produit proposé ou effacez le filtre produit.', 'Select a suggested product or clear the product filter.'))
    const query = new URLSearchParams({ period_days: String(period.value), horizon_days: String(horizon.value), status: status.value, low_stock_threshold: String(threshold.value), discount_bps: String(Math.round(discount.value * 100)), limit: '20', offset: String(offset.value) })
    if (category.value) query.set('category', category.value)
    if (productId.value) query.set('product_id', productId.value)
    const result = await session.client.request('/b2b/api/intelligence?' + query, intelligenceDecoder, { signal: controller.signal })
    if (active !== controller) return
    if (result.organization_id !== session.current.value?.organization_id) throw new Error('scope')
    if (result.horizon_days !== Number(query.get('horizon_days')) || result.offset !== Number(query.get('offset')) || result.limit !== 20 || result.low_stock_threshold !== Number(query.get('low_stock_threshold'))) throw new Error('filters')
    data.value = result
  } catch (cause) {
    if (active === controller && !(cause instanceof StaleResponse) && !controller.signal.aborted) error.value = cause instanceof Error && cause.message.startsWith(i('Sélectionnez', 'Select')) ? cause.message : i('Les données analytiques sont indisponibles. Réessayez ; aucune opération métier n’a été effectuée.', 'Analytics data is unavailable. Retry; no business operation was performed.')
  } finally { if (active === controller) busy.value = false }
}
async function suggest(value: string) {
  sku.value = value; productId.value = ''; suggestions.value = []
  const version = ++suggestionVersion
  if (!allowed.value || value.trim().length < 2) return
  try {
    const result = await session.client.request('/b2b/api/products/suggestions?prefix=' + encodeURIComponent(value.trim()) + '&limit=10', object({ items: array(productDecoder, 10) }))
    if (version === suggestionVersion) suggestions.value = result.items
  } catch { /* Main form retains the explicit unavailable/selection state. */ }
}
function select(value: string) { productId.value = suggestions.value.find(item => item.sku === value)?.product_id ?? '' }
function exportPage() {
  if (!data.value || !allowed.value) return
  const blob = new Blob([JSON.stringify(data.value, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = 'stockpilot-intelligence-page.json'; link.click(); URL.revokeObjectURL(url)
}
onMounted(async () => {
  if (!allowed.value) return
  void load()
  try { categories.value = (await session.client.request('/b2b/api/products/categories', object({ items: array(text, 1000) }))).items } catch { /* Free category selection remains empty, never invent values. */ }
})
onUnmounted(() => { active?.abort(); active = null; suggestionVersion++ })
</script>
<template>
  <section class="intelligence-view">
    <header class="page-heading intelligence-heading">
      <div>
        <p class="eyebrow">
          {{ i('Pilotage commercial', 'Commercial planning') }}
        </p><h1>StockPilot Intelligence</h1><p>{{ i('Des observations traçables, des estimations expliquées.', 'Traceable observations, explained estimates.') }}</p>
      </div>
      <span class="intelligence-readonly">{{ i('Lecture seule', 'Read only') }}</span>
    </header>
    <p
      v-if="!allowed"
      class="b2b-card"
      role="alert"
    >
      {{ i('Ce module est réservé aux managers et administrateurs autorisés.', 'This module is restricted to authorized managers and administrators.') }}
    </p>
    <template v-else>
      <form
        class="b2b-card intelligence-filters"
        @submit.prevent="load(true)"
      >
        <p class="intelligence-org">
          {{ i('Organisation', 'Organization') }} : <strong>{{ session.current.value?.organization_name }}</strong>
        </p>
        <label>{{ i('Période observée', 'Observed period') }}<select v-model="period"><option
          v-for="days in [30,90,180,365]"
          :key="days"
          :value="days"
        >{{ days }} {{ i('jours', 'days') }}</option></select></label>
        <label>{{ i('Horizon de prévision', 'Forecast horizon') }}<select v-model="horizon"><option
          v-for="days in [7,30,60,90]"
          :key="days"
          :value="days"
        >{{ days }} {{ i('jours', 'days') }}</option></select></label>
        <CatalogueCombobox
          id="intelligence-category"
          v-model="category"
          :label="i('Catégorie (vide : toutes)', 'Category (empty: all)')"
          :options="categories.map(value => ({ value, label: value }))"
        />
        <label>{{ i('Statut produit', 'Product status') }}<select v-model="status"><option value="active">{{ i('Actif', 'Active') }}</option><option value="inactive">{{ i('Inactif', 'Inactive') }}</option></select></label>
        <CatalogueCombobox
          id="intelligence-product"
          :label="i('Produit · rechercher un SKU', 'Product · search a SKU')"
          :model-value="sku"
          :options="suggestions.map(item => ({ value: item.sku, label: item.name, detail: item.sku }))"
          @update:model-value="suggest"
          @select="select"
        />
        <label>{{ i('Seuil faible indicatif (unités)', 'Indicative low-stock threshold (units)') }}<input
          v-model.number="threshold"
          type="number"
          min="0"
          max="1000000"
          step="1"
          required
        /></label>
        <label>{{ i('Remise simulée personnalisée (%)', 'Custom simulated discount (%)') }}<input
          v-model.number="discount"
          type="number"
          min="0"
          max="90"
          step="0.01"
          required
        /></label>
        <button :disabled="busy">
          {{ i('Actualiser l’analyse', 'Refresh analysis') }}
        </button>
      </form>
      <p
        v-if="busy"
        role="status"
        class="b2b-card intelligence-loading"
      >
        {{ i('Lecture des sources et calcul des estimations…', 'Reading sources and calculating estimates…') }}
      </p>
      <div
        v-if="error"
        role="alert"
        class="b2b-card"
      >
        <p>{{ error }}</p><button @click="load()">
          {{ i('Réessayer', 'Retry') }}
        </button>
      </div>
      <template v-if="data">
        <p class="intelligence-source">
          {{ i('Actualisé', 'Updated') }} {{ dateLabel(data.refreshed_at) }} · {{ data.period_start }} → {{ data.period_end_exclusive }} {{ i('(fin exclue)', '(end excluded)') }} · {{ i('Source stock : catalogue autorisé', 'Stock source: authorized catalogue') }}
        </p>
        <p
          v-if="synthetic"
          class="b2b-notice"
          role="status"
        >
          DEMO · {{ i('Historique synthétique de test. Ces prévisions ne décrivent pas des ventes réelles.', 'Synthetic test history. These forecasts do not describe real sales.') }}
        </p>
        <div class="intelligence-kpis">
          <article class="b2b-card">
            <span>{{ i('Produits filtrés', 'Filtered products') }}</span><strong>{{ number(data.summary.products) }}</strong><small>{{ i('Tous les résultats · statut sélectionné', 'All results · selected status') }}</small>
          </article>
          <article class="b2b-card">
            <span>{{ i('Stock disponible', 'Available stock') }}</span><strong>{{ number(data.summary.available) }}</strong><small>{{ number(data.summary.reserved) }} {{ i('réservées · unités', 'reserved · units') }}</small>
          </article>
          <article class="b2b-card">
            <span>{{ i('En rupture', 'Out of stock') }}</span><strong>{{ number(data.summary.out_of_stock) }}</strong><small>{{ i('Disponible = 0', 'Available = 0') }}</small>
          </article>
          <article class="b2b-card">
            <span>{{ i('Stock faible', 'Low stock') }}</span><strong>{{ number(data.summary.low_stock) }}</strong><small>0 &lt; {{ i('disponible', 'available') }} ≤ {{ data.low_stock_threshold }}</small>
          </article>
        </div>
        <section class="b2b-card intelligence-value">
          <h2>{{ i('Valeur au tarif courant', 'Value at current list prices') }}</h2><p
            v-for="value in data.summary.retail_value_by_currency"
            :key="value.currency"
          >
            <strong>{{ value.amount_minor === null ? i('Valeur hors plage exacte', 'Value beyond exact range') + ' · ' + value.currency : analyticMoney(value.amount_minor, value.currency) }}</strong>
          </p><p class="hint">
            {{ i('Stock physique × prix catalogue. Ce montant ne représente ni le coût d’achat, ni le chiffre d’affaires, ni une marge. Les devises restent séparées.', 'Physical stock × catalogue price. This is neither purchase cost, revenue nor margin. Currencies remain separate.') }}
          </p>
        </section>
        <p
          v-if="!data.items.length"
          class="b2b-card"
        >
          {{ i('Aucun produit ne correspond à ces filtres.', 'No products match these filters.') }}
        </p>
        <template v-else>
          <section class="b2b-card">
            <h2>{{ i('Priorités de cette page', 'Priorities on this page') }}</h2><p class="hint">
              {{ i('Suggestions à examiner ; aucun achat ni mouvement automatique.', 'Suggestions for review; no automatic purchase or stock movement.') }}
            </p><ul class="intelligence-priorities">
              <li
                v-for="item in recommendations.slice(0, 6)"
                :key="item.product_id"
              >
                <button
                  class="secondary"
                  @click="detailId = item.product_id"
                >
                  {{ item.sku }} · {{ item.name }}
                </button><span>{{ item.reasons.map(reasonLabel).join(' · ') }}</span>
              </li>
            </ul><p v-if="!recommendations.length">
              {{ i('Aucune alerte établie sur cette page.', 'No established alert on this page.') }}
            </p>
          </section>
          <section class="b2b-card">
            <h2>{{ i('Demande et mouvements qualifiés', 'Qualified demand and movements') }}</h2><p class="hint">
              {{ i('Séries limitées aux produits de cette page. Un jour absent ou incomplet ne vaut pas zéro. Réservations, retours et ajustements restent distincts des ventes.', 'Series cover products on this page only. A missing or incomplete day is not zero. Reservations, returns and adjustments remain separate from sales.') }}
            </p>
            <div
              v-if="data.page_series.length"
              class="intelligence-charts"
            >
              <IntelligenceChart
                :title="i('Ventes réalisées', 'Completed sales')"
                :values="data.page_series.map(row => row.observed_products === data!.items.length ? row.sold : null)"
                :labels="data.page_series.map(row => row.date)"
                :unit="i('unités / jour', 'units / day')"
              />
              <IntelligenceChart
                :title="i('Ajustements d’inventaire', 'Inventory adjustments')"
                :values="data.page_series.map(row => row.observed_products === data!.items.length ? row.adjusted : null)"
                :labels="data.page_series.map(row => row.date)"
                :unit="i('unités / jour', 'units / day')"
              />
            </div><p v-else>
              {{ i('Aucun historique commercial qualifié. Les réservations existantes ne sont pas converties en ventes.', 'No qualified commercial history. Existing reservations are not converted into sales.') }}
            </p>
            <details v-if="data.page_series.length">
              <summary>{{ i('Consulter les valeurs et la complétude', 'View values and completeness') }}</summary><div
                class="b2b-table"
                tabindex="0"
                role="region"
                :aria-label="i('Historique des mouvements', 'Movement history')"
              >
                <table>
                  <thead><tr><th>Date</th><th>{{ i('Ventes', 'Sales') }}</th><th>{{ i('Retours', 'Returns') }}</th><th>{{ i('Réceptions', 'Receipts') }}</th><th>{{ i('Réservations', 'Reservations') }}</th><th>{{ i('Annulations', 'Cancellations') }}</th><th>{{ i('Ajustements', 'Adjustments') }}</th><th>{{ i('Transferts', 'Transfers') }}</th><th>{{ i('Produits observés', 'Observed products') }}</th></tr></thead><tbody>
                    <tr
                      v-for="row in data.page_series"
                      :key="row.date"
                    >
                      <td>{{ row.date }}</td><td>{{ number(row.observed_products ? row.sold : null) }}</td><td>{{ number(row.observed_products ? row.returned : null) }}</td><td>{{ number(row.observed_products ? row.received : null) }}</td><td>{{ number(row.observed_products ? row.reserved : null) }}</td><td>{{ number(row.observed_products ? row.cancelled : null) }}</td><td>{{ number(row.observed_products ? row.adjusted : null) }}</td><td>{{ number(row.observed_products ? row.transferred : null) }}</td><td>{{ row.observed_products }} / {{ data.items.length }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>
          </section>
          <section class="b2b-card">
            <h2>{{ i('Activité et couverture — cette page', 'Activity and coverage — this page') }}</h2>
            <p>{{ i('Couverture moyenne calculable', 'Mean calculable coverage') }} : <strong>{{ number(data.page_summary.mean_coverage_days) }} {{ i('jours', 'days') }}</strong>. {{ i('Les produits sans estimation sont exclus de cette moyenne.', 'Products without an estimate are excluded from this mean.') }}</p>
            <h3>{{ i('Ventes observées les plus élevées', 'Highest observed sales') }}</h3>
            <ol v-if="bestSales.length">
              <li
                v-for="item in bestSales"
                :key="item.product_id"
              >
                {{ item.sku }} · {{ number(item.period_sales_units) }} {{ i('unités sur la période', 'units in period') }}
              </li>
            </ol>
            <p v-else>
              {{ i('Historique complet nécessaire pour comparer les ventes.', 'Complete history is required to compare sales.') }}
            </p>
          </section>
          <section class="b2b-card">
            <h2>{{ i('Produits et recommandations', 'Products and recommendations') }}</h2><p class="hint">
              {{ i('Page courante uniquement', 'Current page only') }} · {{ data.page_summary.forecastable_products }} {{ i('prévisions calculables', 'forecastable products') }} · {{ data.page_summary.dormant_products }} {{ i('sans vente observée', 'with no observed sales') }}
            </p><div
              class="b2b-table"
              tabindex="0"
              role="region"
              :aria-label="i('Tableau analytique', 'Analytics table')"
            >
              <table>
                <thead><tr><th>{{ i('Produit', 'Product') }}</th><th>{{ i('Disponible', 'Available') }}</th><th>{{ i('Ventes sur la période', 'Sales in period') }}</th><th>{{ i('Couverture estimée (jours)', 'Estimated coverage (days)') }}</th><th>{{ i('Quantité conseillée', 'Suggested quantity') }}</th><th>{{ i('Qualité', 'Quality') }}</th><th>{{ i('Détails', 'Details') }}</th></tr></thead><tbody>
                  <tr
                    v-for="item in data.items"
                    :key="item.product_id"
                  >
                    <th scope="row">
                      {{ item.name }}<small>{{ item.sku }}</small>
                    </th><td>{{ number(item.available) }}</td><td>{{ number(item.period_sales_units) }}</td><td>
                      {{ number(item.replenishment.coverage_days) }}<meter
                        v-if="item.replenishment.coverage_days !== null"
                        min="0"
                        :max="Math.max(90, item.replenishment.coverage_days)"
                        :value="item.replenishment.coverage_days"
                        :aria-label="i('Couverture estimée en jours', 'Estimated coverage in days')"
                      ></meter>
                    </td><td>{{ number(item.replenishment.suggested_quantity) }}</td><td>{{ forecastLabel(item.forecast.status) }}</td><td>
                      <button
                        class="secondary"
                        :aria-label="i('Analyser ', 'Analyze ') + item.sku"
                        @click="detailId = item.product_id"
                      >
                        {{ i('Analyser', 'Analyze') }}
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
          <section class="b2b-card">
            <h2>{{ i('ABC de la valeur de stock — cette page', 'Stock value ABC — this page') }}</h2><p>{{ i('Classement des coûts d’achat connus, dans une seule devise : A jusqu’à 80 % cumulés, B jusqu’à 95 %, C au-delà ; le produit qui franchit le seuil reste dans sa classe. Ce n’est pas une analyse ABC de consommation annuelle.', 'Ranking of known purchase costs in one currency: A up to cumulative 80%, B to 95%, C beyond; the product crossing a threshold stays in its class. This is not annual consumption ABC.') }}</p><p v-if="!abc">
              {{ i('Indisponible : tous les coûts doivent être connus, positifs au total et dans une seule devise.', 'Unavailable: all costs must be known, positive in total and in one currency.') }}
            </p><ul
              v-else
              class="intelligence-abc"
            >
              <li
                v-for="item in data.items"
                :key="item.product_id"
              >
                <strong>{{ abc.get(item.product_id) }}</strong> {{ item.sku }} · {{ analyticMoney(item.stock_cost_minor!, item.currency) }}
              </li>
            </ul>
          </section>
        </template>
        <nav
          class="b2b-actions"
          :aria-label="i('Pagination analytique', 'Analytics pagination')"
        >
          <button
            class="secondary"
            :disabled="busy || offset === 0"
            @click="offset = Math.max(0, offset - 20); load()"
          >
            {{ i('Précédent', 'Previous') }}
          </button><span>{{ data.offset + (data.items.length ? 1 : 0) }}–{{ data.offset + data.items.length }} / {{ data.total }}</span><button
            class="secondary"
            :disabled="busy || !data.has_more"
            @click="offset += 20; load()"
          >
            {{ i('Suivant', 'Next') }}
          </button><button
            class="secondary"
            @click="exportPage"
          >
            {{ i('Exporter cette page (JSON)', 'Export this page (JSON)') }}
          </button>
        </nav>
        <aside class="b2b-notice">
          <h2>{{ i('Ce que ces résultats permettent de conclure', 'What these results support') }}</h2><p>{{ i('Les explications proviennent de règles vérifiables, sans génération de chiffres par IA. Le module reste utilisable sans Ollama. L’âge des lots, la saisonnalité annuelle et la rotation réelle ne sont pas établis par les données actuelles.', 'Explanations follow verifiable rules; AI does not generate figures. The module works without Ollama. Lot age, annual seasonality and true inventory turnover are not established by current data.') }}</p>
        </aside>
      </template>
    </template>
    <DetailsPanel
      v-if="chosen"
      :title="chosen.name + ' · ' + chosen.sku"
      :label="i('Analyse du produit', 'Product analysis')"
      @close="detailId = null"
    >
      <div class="intelligence-detail">
        <p
          v-if="chosen.dataset === 'DEMO_SYNTHETIC'"
          class="b2b-notice"
        >
          DEMO · {{ i('Données synthétiques', 'Synthetic data') }}
        </p>
        <h3>{{ i('Observations', 'Observations') }}</h3><dl><div><dt>{{ i('Disponible / réservé / physique', 'Available / reserved / physical') }}</dt><dd>{{ number(chosen.available) }} / {{ number(chosen.reserved) }} / {{ number(chosen.quantity) }}</dd></div><div><dt>{{ i('Ventes / retours sur la période', 'Sales / returns in period') }}</dt><dd>{{ number(chosen.period_sales_units) }} / {{ number(chosen.period_returns_units) }}</dd></div><div><dt>{{ i('Moyenne des ventes (unités/jour)', 'Mean sales (units/day)') }}</dt><dd>{{ number(chosen.mean_daily_sales) }}</dd></div><div><dt>{{ i('Coût documenté du stock', 'Documented stock cost') }}</dt><dd>{{ chosen.stock_cost_minor === null ? missing() : analyticMoney(chosen.stock_cost_minor, chosen.currency) }}</dd></div></dl>
        <h3>{{ i('Prévision et erreur mesurée', 'Forecast and measured error') }}</h3><p>{{ forecastLabel(chosen.forecast.status) }} · {{ chosen.history_days }} {{ i('jours documentés', 'documented days') }}</p>
        <template v-if="chosen.forecast.status === 'estimated'">
          <p><strong>{{ number(chosen.forecast.quantity) }} {{ i('unités estimées sur', 'estimated units over') }} {{ chosen.forecast.horizon_days }} {{ i('jours', 'days') }}</strong> · {{ chosen.forecast.method }}</p><IntelligenceChart
            :title="i('Profil quotidien prévu', 'Forecast daily profile')"
            :values="chosen.forecast.daily"
            :labels="chosen.forecast.daily.map((_, index) => String(index + 1))"
            :unit="i('unités / jour futur', 'units / future day')"
          /><p>{{ i('Backtest temporel : erreur absolue moyenne', 'Temporal backtest: mean absolute error') }} {{ number(chosen.forecast.backtest?.mae_units) }} · {{ i('référence naïve', 'naive baseline') }} {{ number(chosen.forecast.backtest?.naive_mae_units) }} · RMSE {{ number(chosen.forecast.backtest?.rmse_units) }} {{ i('unités par horizon', 'units per horizon') }}.</p><p>{{ chosen.forecast.backtest?.assessment_windows }} {{ i('fenêtres d’évaluation hors sélection du modèle', 'assessment windows separate from model selection') }} · {{ chosen.forecast.backtest?.assessment_start }} → {{ chosen.forecast.backtest?.assessment_end }}</p><p v-if="chosen.forecast.uncertainty">
            {{ i('Plage fondée sur les erreurs historiques', 'Historical error range') }} : {{ number(chosen.forecast.uncertainty.lower) }}–{{ number(chosen.forecast.uncertainty.upper) }}. {{ i('Aucune probabilité de couverture garantie.', 'No guaranteed coverage probability.') }}
          </p><p v-else>
            {{ i('Incertitude chiffrée non disponible : trop peu de fenêtres indépendantes.', 'Numeric uncertainty unavailable: too few independent windows.') }}
          </p>
        </template>
        <h3>{{ i('Hypothèses et réapprovisionnement', 'Assumptions and replenishment') }}</h3><dl><div><dt>{{ i('Fournisseur', 'Supplier') }}</dt><dd>{{ chosen.supplier ?? missing() }}</dd></div><div><dt>{{ i('Délai (jours) / stock de sécurité / minimum', 'Lead time (days) / safety stock / minimum') }}</dt><dd>{{ number(chosen.supply_parameters?.lead_time_days) }} / {{ number(chosen.supply_parameters?.safety_stock) }} / {{ number(chosen.supply_parameters?.minimum_order_quantity) }}</dd></div><div><dt>{{ i('Entrées confirmées / date de vérification', 'Confirmed incoming / verification date') }}</dt><dd>{{ number(chosen.supply_parameters?.incoming_confirmed) }} / {{ chosen.supply_parameters?.incoming_as_of ?? missing() }}</dd></div><div><dt>{{ i('Point de commande / quantité conseillée', 'Reorder point / suggested quantity') }}</dt><dd>{{ number(chosen.replenishment.reorder_point) }} / {{ number(chosen.replenishment.suggested_quantity) }}</dd></div><div><dt>{{ i('Couverture indicative sans entrées (jours)', 'Indicative coverage without receipts (days)') }}</dt><dd>{{ number(chosen.replenishment.coverage_days) }}</dd></div></dl><p>{{ i('Point = demande moyenne prévue × délai + sécurité. Quantité = besoin sur le plus grand du délai et de l’horizon, moins disponible et entrées confirmées, avec minimum fournisseur. Une confirmation d’entrées ancienne empêche une recommandation chiffrée. Il ne s’agit jamais d’une commande.', 'Point = forecast daily mean × lead time + safety. Quantity = need over the longer of lead time and horizon, less available stock and confirmed receipts, respecting the supplier minimum. Stale incoming confirmation prevents a numeric recommendation. This is never an order.') }}</p>
        <h3>{{ i('Opportunités commerciales · simulation', 'Commercial opportunities · simulation') }}</h3><p>{{ chosen.dormant ? i('Aucune vente observée sur une période complète sans rupture : examiner une opération commerciale.', 'No observed sales across a complete period without stockouts: consider reviewing a commercial offer.') : i('Aucune éligibilité promotionnelle n’est déduite automatiquement.', 'No promotional eligibility is inferred automatically.') }}</p><div
          class="b2b-table"
          tabindex="0"
          role="region"
          :aria-label="i('Simulations de remise', 'Discount simulations')"
        >
          <table>
            <thead><tr><th>{{ i('Remise hypothétique', 'Hypothetical discount') }}</th><th>{{ i('Prix simulé', 'Simulated price') }}</th><th>{{ i('Écart prix − coût unitaire', 'Price − unit cost') }}</th></tr></thead><tbody>
              <tr
                v-for="scenario in chosen.promotion_scenarios"
                :key="scenario.discount_bps"
              >
                <td>{{ scenario.discount_bps / 100 }} %</td><td>{{ analyticMoney(scenario.price_minor, chosen.currency) }}</td><td>{{ scenario.unit_margin_minor === null ? missing() : analyticMoney(scenario.unit_margin_minor, chosen.currency) }}</td>
              </tr>
            </tbody>
          </table>
        </div><p class="hint">
          {{ i('Scénarios internes au tarif courant, hors taxes, frais et variation de demande. Aucune élasticité-prix ni rentabilité estimée. Ce n’est pas une annonce de réduction : l’historique de prix de référence et les règles applicables doivent être vérifiés avant publication.', 'Internal scenarios at current list prices, excluding taxes, fees and demand changes. No price elasticity or profitability estimate. This is not a discount announcement: reference-price history and applicable rules must be checked before publication.') }}
        </p>
        <details>
          <summary>{{ i('Sources et qualité', 'Sources and quality') }}</summary><p>{{ chosen.dataset }} · {{ chosen.source_reference_count }} {{ i('références, 20 au maximum affichées', 'references, up to 20 shown') }} · {{ chosen.period_complete ? i('Période complète', 'Complete period') : i('Période incomplète', 'Incomplete period') }}</p><ul>
            <li
              v-for="source in chosen.source_references"
              :key="source"
            >
              {{ source }}
            </li>
          </ul><p>{{ i('Paramètres fournisseur', 'Supply parameters') }} : {{ chosen.supply_parameters?.source_references.join(', ') ?? missing() }}</p>
        </details>
      </div>
    </DetailsPanel>
  </section>
</template>
<style scoped>
.intelligence-heading { display:flex; align-items:start; justify-content:space-between; gap:20px; }
.intelligence-readonly { border:1px solid var(--line); border-radius:30px; padding:8px 14px; color:var(--muted); white-space:nowrap; }
.intelligence-filters { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; align-items:end; }
.intelligence-filters label { display:grid; gap:7px; min-width:0; }
.intelligence-filters input,.intelligence-filters select { width:100%; min-height:44px; box-sizing:border-box; }
.intelligence-org { grid-column:1/-1; margin:0; }
.intelligence-kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }
.intelligence-kpis article { display:grid; gap:10px; }
.intelligence-kpis strong { font-size:2rem; letter-spacing:-.04em; }
.intelligence-kpis small,.intelligence-source { color:var(--muted); }
.intelligence-value strong { font-size:1.5rem; }
.intelligence-charts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:32px; margin:24px 0; }
.intelligence-priorities { list-style:none; padding:0; display:grid; gap:12px; }
.intelligence-priorities li { display:grid; grid-template-columns:minmax(180px,1fr) 2fr; gap:16px; align-items:center; border-bottom:1px solid var(--line); padding-bottom:12px; }
.intelligence-view td,.intelligence-view th { min-width:100px; }
.intelligence-view th small,.intelligence-view meter { display:block; margin-top:6px; }
.intelligence-view .b2b-table { max-width:100%; }
.intelligence-view .b2b-table button { white-space:nowrap; overflow-wrap:normal; }
.intelligence-abc { display:flex; gap:20px; flex-wrap:wrap; list-style:none; padding:0; }
.intelligence-detail dl { display:grid; gap:14px; }
.intelligence-detail dl>div { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.intelligence-detail dt { color:var(--muted); }.intelligence-detail dd { margin:0; font-weight:600; }
.intelligence-detail h3 { margin:28px 0 14px; }.intelligence-detail p { line-height:1.65; }
.intelligence-view button { min-height:44px; }
@media(max-width:800px) { .intelligence-filters,.intelligence-kpis { grid-template-columns:repeat(2,minmax(0,1fr)); } .intelligence-charts { grid-template-columns:1fr; } }
@media(max-width:480px) { .intelligence-heading { display:block; } .intelligence-readonly { display:inline-block; margin-bottom:16px; } .intelligence-filters { grid-template-columns:1fr; } .intelligence-kpis { gap:10px; }.intelligence-kpis article { padding:12px; }.intelligence-priorities li { grid-template-columns:1fr; }.intelligence-detail dl>div { grid-template-columns:1fr; gap:4px; } }
</style>
