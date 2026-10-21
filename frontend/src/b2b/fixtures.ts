// Exact wire fixtures shared by B2B tests. No permissive partial response mocks.
export const sessionFixture = {
  user_id: 'user-a', organization_id: 'org-a', organization_name: 'Entreprise A', role: 'ORGANIZATION_ADMIN',
  permissions: ['catalogue:read', 'stock:read', 'stock:adjust', 'order:create', 'order:read_own', 'order:read_organization', 'order:cancel_own', 'import:execute'],
  csrf_token: 'test-csrf', expires_at: '2099-01-01T01:00:00Z', idle_expires_at: '2099-01-01T00:10:00Z',
}
export const productFixture = {
  product_id: 'product-a', organization_id: 'org-a', sku: 'DRILL-001', name: 'Perceuse A',
  description: 'Produit vérifié', category: 'Outils', price_minor: '9007199254740993', currency: 'EUR',
  status: 'active', attributes: [], revision: 1,
}
export const stockFixture = { product_id: 'product-a', organization_id: 'org-a', quantity: 10, reserved: 0, available: 10, revision: 1, status: 'active' }
export const orderFixture = {
  organization_id: 'org-a', order_id: 'order-a', customer_id: 'buyer-a', created_by: 'user-a', status: 'prepared',
  lines: [{ product_id: 'product-a', sku: 'DRILL-001', quantity: 1, price_minor: '4900', currency: 'EUR' }],
  operation_id: 'original-prepare', preparation_digest: 'digest', created_at: '2026-10-21T10:00:00Z',
  expires_at: '2099-01-01T00:00:00Z', reserved_at: null, cancelled_at: null, revision: 1,
}
export const buyerFixture = { association_id: 'association-a', organization_id: 'org-a', user_id: 'user-a', customer_id: 'buyer-a', status: 'active', revision: '1' }
export const previewFixture = {
  import_id: 'import-a', organization_id: 'org-a', actor_user_id: 'user-a', mode: 'stock_only', status: 'preview',
  file_fingerprint: 'hash', preview_digest: 'digest', rows: 1, issues: [], expires_at: '2099-01-01T00:00:00Z',
}
export const resultFixture = {
  operation_id: 'original-adjust', namespace: 'adjust', organization_id: 'org-a', actor_user_id: 'user-a',
  status: 'succeeded', code: 'adjusted', movement_ids: ['movement-a'], import_id: null,
}
export const list = (items: unknown[]) => ({ items, limit: 10, offset: 0 })
export const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
