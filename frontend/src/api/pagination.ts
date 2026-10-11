import { ApiError } from './http'
export function validatePagination(page: { total?: number; count: number; has_more?: boolean }) {
  if (page.total !== undefined && (!Number.isSafeInteger(page.total) || page.total < 0 || page.total < page.count)) {
    throw new ApiError('Total de pagination invalide.', 200)
  }
  if (page.has_more !== undefined && typeof page.has_more !== 'boolean') throw new ApiError('Pagination invalide.', 200)
}
