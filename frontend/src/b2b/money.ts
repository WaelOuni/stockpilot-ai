import { currency as validateCurrency, minor } from './contracts'
import { language, t } from './preferences'

// Only the currency's minor-unit exponent is numeric. Monetary values stay BigInt.
export function formatMoney(value: string, currency: string, locale = language.value === 'en' ? 'en-GB' : 'fr-FR'): string {
  minor(value)
  return formatAmount(BigInt(value), currency, locale)
}

// Totals can exceed one stored unit price. Keep wire validation unchanged and
// accept only locally calculated nonnegative BigInt amounts in this formatter.
export function formatMoneyTotal(value: bigint, currency: string, locale = language.value === 'en' ? 'en-GB' : 'fr-FR'): string {
  if (typeof value !== 'bigint' || value < 0n || value > 20n * 9223372036854775807n * BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('Montant total invalide.')
  return formatAmount(value, currency, locale)
}

function formatAmount(amount: bigint, currency: string, locale: string): string {
  validateCurrency(currency)
  if (!Intl.supportedValuesOf('currency').includes(currency)) return amount.toString() + ' ' + t('unités mineures (') + currency + t(', précision inconnue)')
  const formatter = new Intl.NumberFormat(locale, { style: 'currency', currency })
  const digits = formatter.resolvedOptions().maximumFractionDigits
  if (digits === undefined) throw new Error('Unité monétaire inconnue.')
  const scale = 10n ** BigInt(digits)
  const fraction = (amount % scale).toString().padStart(digits, '0')
  return formatter.formatToParts(amount / scale)
    .map(part => part.type === 'fraction' ? fraction : part.value).join('')
}
