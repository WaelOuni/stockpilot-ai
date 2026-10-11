import type { ProductCategory } from '../types/api'
import type { LocationQuery } from 'vue-router'

export const productCategoryLabels: Record<ProductCategory, string> = {
  DRILL: 'Perceuse', SAW: 'Scie', SANDER: 'Ponceuse', SCREWDRIVER: 'Visseuse', OTHER: 'Autre',
}

export function catalogueQuery(query: LocationQuery): Record<string, string> {
  const result: Record<string, string> = {}
  if (typeof query.q === 'string' && query.q.trim() && query.q.length <= 100) {
    result.q = query.q
  }
  if (typeof query.category === 'string' && Object.hasOwn(productCategoryLabels, query.category)) {
    result.category = query.category
  }
  if (query.available === 'true' || query.available === 'false') {
    result.available = query.available
  }
  if (typeof query.offset === 'string' && /^\d+$/.test(query.offset) &&
      Number.isSafeInteger(Number(query.offset))) {
    result.offset = query.offset
  }
  if (typeof query.limit === 'string' && [10, 20, 50, 100].includes(Number(query.limit))) result.limit = query.limit
  return result
}
