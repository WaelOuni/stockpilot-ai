import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, type Page } from '@playwright/test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')

export function syntheticCustomerToken(customer: string): string {
  const python = process.env.E2E_PYTHON || resolve(root, '.venv/bin/python')
  try {
    return execFileSync(python, [
      resolve(root, 'scripts/create_customer_token.py'), customer, '--expires-in', '1800',
    ], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  } catch {
    throw new Error('Synthetic customer token generation failed; no credentials logged.')
  }
}

export function customerHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` }
}

export async function connectCustomer(page: Page, token: string): Promise<void> {
  // Full navigation/reload clears the application's in-memory customer token.
  await page.getByLabel('JWT client', { exact: true }).fill(token)
  await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  await expect(page.getByLabel('JWT client', { exact: true })).toHaveValue('')
}
