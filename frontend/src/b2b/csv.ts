export type CsvMode = 'stock_only' | 'metadata_only' | 'create_only'
export const csvHeaders: Record<CsvMode, string[]> = {
  stock_only: ['sku', 'quantity'],
  metadata_only: ['sku', 'name', 'description', 'category', 'price_minor', 'currency'],
  create_only: ['sku', 'name', 'description', 'category', 'price_minor', 'currency', 'quantity'],
}
// RFC 4180 quoting. For spreadsheet downloads, neutralize executable cells too.
export function encodeCsv(rows: (string | number)[][], spreadsheet = false): string {
  return rows.map(row => row.map(value => {
    let text = String(value)
    if (spreadsheet && /^[\s]*[=+@-]/.test(text)) text = "'" + text
    return '"' + text.replaceAll('"', '""') + '"'
  }).join(',')).join('\r\n') + '\r\n'
}
export function downloadCsv(filename: string, rows: (string | number)[][]) {
  const url = URL.createObjectURL(new Blob(['\ufeff', encodeCsv(rows, true)], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a'); link.href = url; link.download = filename
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export function priceMinor(value: string, currency: string): string {
  const digits = new Intl.NumberFormat('fr', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2
  const normalized = value.trim().replace(',', '.')
  const match = /^(0|[1-9]\d*)(?:\.(\d+))?$/.exec(normalized)
  if (!match || (match[2]?.length ?? 0) > digits) throw new Error('Saisissez un prix positif avec la précision de la devise.')
  const amount = BigInt(match[1]!) * 10n ** BigInt(digits) + BigInt((match[2] ?? '').padEnd(digits, '0') || '0')
  if (amount > 9223372036854775807n) throw new Error('Le prix dépasse la limite autorisée.')
  return amount.toString()
}
export function priceInput(value: string, currency: string): string {
  const digits = new Intl.NumberFormat('fr', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2
  const scale = 10n ** BigInt(digits); const amount = BigInt(value)
  return String(amount / scale) + (digits ? '.' + String(amount % scale).padStart(digits, '0') : '')
}
