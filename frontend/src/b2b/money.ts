import { currency as validateCurrency, minor } from './contracts'

// Only the currency's minor-unit exponent is numeric. Monetary values stay BigInt.
export function formatMoney(value: string, currency: string, locale = 'fr-FR'): string {
  minor(value)
  validateCurrency(currency)
  if (!Intl.supportedValuesOf('currency').includes(currency)) return value + ' unités mineures (' + currency + ', précision inconnue)'
  const formatter = new Intl.NumberFormat(locale, { style: 'currency', currency })
  const digits = formatter.resolvedOptions().maximumFractionDigits
  if (digits === undefined) throw new Error('Unité monétaire inconnue.')
  const scale = 10n ** BigInt(digits)
  const amount = BigInt(value)
  const fraction = (amount % scale).toString().padStart(digits, '0')
  return formatter.formatToParts(amount / scale)
    .map(part => part.type === 'fraction' ? fraction : part.value).join('')
}
