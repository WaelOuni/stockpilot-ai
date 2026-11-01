// Real Keycloak/PKCE, Mongo, PostgreSQL and built Vue. No intercepted wire responses.
import process from 'node:process'
import { chromium, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
const origin = process.env.B2B_TEST_ORIGIN
const issuer = process.env.B2B_TEST_ISSUER_ORIGIN
const checks = [], writes = [], errors = []
const browser = await chromium.launch({ headless: true, args: ['--ignore-certificate-errors-spki-list=' + process.env.B2B_BROWSER_SPKI] })
const context = await browser.newContext(), page = await context.newPage()
context.on('request', r => { if(r.method() !== 'GET' && r.url().startsWith(origin)) writes.push(new globalThis.URL(r.url()).pathname) })
page.on('pageerror', () => errors.push('pageerror'))
const check = name => checks.push(name)
const probe = stage => execFileSync(process.env.B2B_TEST_PYTHON, ['scripts/tests/b2b_browser_probe.py', stage], { cwd: '..', env: process.env, stdio: 'pipe' })
async function login(org, user, navigate=true) {
  if(navigate) await page.goto(origin + '/b2b-app/')
  await page.getByLabel('Code d’entreprise').fill(org)
  await page.getByRole('button', {name:'Continuer avec Keycloak',exact:true}).click()
  await page.waitForURL(issuer+'/**')
  expect(new globalThis.URL(page.url()).searchParams.get('code_challenge_method')).toBe('S256')
  expect(new globalThis.URL(page.url()).searchParams.get('prompt')).toBe('login')
  if(await page.locator('#username').isVisible()) await page.locator('#username').fill(user)
  await page.locator('#password').fill(process.env.BFF_TEST_USER_PASSWORD)
  await page.locator('#kc-login').click()
  await page.waitForURL(url=>url.origin===origin && url.pathname.startsWith('/b2b-app/'))
  await expect(page.locator('.account-label')).toBeVisible()
}
async function authStatus(){return page.evaluate(async()=> (await globalThis.fetch('/b2b/auth/session')).status)}
try {
  await login('fixture-org','bff-test-user')
  const cookie=(await context.cookies(origin)).find(c=>c.name==='__Host-stockpilot_bff')
  expect(cookie).toMatchObject({secure:true,httpOnly:true,sameSite:'Lax',path:'/'})
  check('real confidential OIDC/PKCE S256 login; Secure HttpOnly host cookie; explicit prompt=login')
  await page.getByRole('navigation').getByRole('link',{name:'Assistant',exact:true}).click()
  await page.getByRole('button',{name:'Nouvelle conversation',exact:true}).click()
  await page.waitForURL(/\/assistant\/[a-z0-9]+$/)
  const first = page.url(), conversation = first.split('/').at(-1)
  await expect(page.getByLabel('Votre message')).toBeVisible()
  await page.getByRole('navigation').getByRole('link',{name:'Catalogue',exact:true}).click()
  await page.getByRole('button',{name:'Assistant IA',exact:true}).click()
  await expect(page.getByRole('dialog').getByLabel('Votre message')).toBeVisible()
  await expect(page.getByRole('dialog').getByLabel('Mes conversations')).toHaveValue(conversation)
  await page.keyboard.press('Escape')
  await page.getByRole('navigation').getByRole('link',{name:'Assistant',exact:true}).click()
  await expect(page).toHaveURL(first)
  await page.reload(); await expect(page.getByLabel('Votre message')).toBeVisible()
  await expect(page.getByLabel('Mes conversations')).toHaveValue(conversation)
  check('active conversation survives catalogue/menu/floating dock/browser reload without creation or resume')
  const csrf=await page.evaluate(async()=> (await (await globalThis.fetch('/b2b/auth/session')).json()).csrf_token)
  const denied=await page.evaluate(async()=> (await globalThis.fetch('/b2b/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status)
  expect(denied).toBe(403); expect(await authStatus()).toBe(200)
  check('missing logout CSRF refused and existing session preserved')
  const second=await context.newPage(); await second.goto(first); await expect(second.getByLabel('Votre message')).toBeVisible()
  probe('expire') // Proven owned tmpfs DB only: expiry fault injection, not a simulated provider.
  await page.reload(); await expect(page.getByLabel('Code d’entreprise')).toBeVisible(); expect(await authStatus()).toBe(401)
  await login('fixture-org','bff-test-user',false)
  await expect(page).toHaveURL(first); await expect(page.getByLabel('Votre message')).toBeVisible()
  check('real Mongo expiry refused; explicit real OIDC reauthentication returns to same authorized conversation')
  await page.locator('.workspace-account summary').click()
  await page.getByRole('button',{name:'Se déconnecter',exact:true}).click()
  await expect(page.getByLabel('Code d’entreprise')).toBeVisible()
  await expect(second.getByLabel('Code d’entreprise')).toBeVisible()
  // Local content is cleared before the asynchronous server revocation completes.
  await expect.poll(authStatus).toBe(401)
  check('voluntary BFF logout revokes cookie and clears both tabs; no automatic relogin')
  await login('fixture-org','bff-test-user')
  await page.locator('.workspace-account summary').click()
  await page.getByRole('button',{name:'Changer de compte',exact:true}).click()
  await page.waitForURL(issuer+'/**')
  if(await page.locator('#kc-logout').isVisible()) await page.locator('#kc-logout').click()
  await page.waitForURL(origin+'/b2b-app/')
  expect(await authStatus()).toBe(401)
  check('real Change account follows provider logout and returns to the fixed logged-out path')
  await login('fixture-other','bff-unprovisioned')
  await page.goto(first); await expect(page.getByRole('alert')).toContainText('Ressource introuvable')
  expect(await page.locator('.assistant-turn').count()).toBe(0)
  expect(await page.evaluate(()=>globalThis.sessionStorage.getItem('stockpilot.b2b.conversation.v1'))).toBeNull()
  check('real second account/organization cannot read or restore first account conversation')
  expect(writes.filter(p=>p.startsWith('/b2b/api/') && p!=='/b2b/api/assistant/conversations')).toEqual([])
  expect(writes.filter(p=>p==='/b2b/api/assistant/conversations')).toHaveLength(1)
  expect(await page.evaluate(()=>Object.keys(globalThis.localStorage))).toEqual(['stockpilot.preferences.v1'])
  expect(errors).toEqual([])
  check('exactly one explicit conversation creation; zero ORDER/CSV/message/resume/retry mutations across navigation, expiry and login')
  await page.screenshot({path:process.env.B2B_BROWSER_DIRECTORY+'/sso-account-isolation.png'})
  writeFileSync(process.env.B2B_BROWSER_REPORT,JSON.stringify({passed:true,checks,mutation_paths:writes,csrf_present:!!csrf,expiration_method:'owned Mongo fault injection; no elapsed 30-minute claim'},null,2))
} catch(error){
  // Private diagnostics only, no credential or token printed in the public report.
  writeFileSync(process.env.B2B_BROWSER_DIRECTORY+'/continuity-private-error.txt',String(error))
  writeFileSync(process.env.B2B_BROWSER_REPORT,JSON.stringify({passed:false,checks,error_type:error.constructor.name},null,2));process.exitCode=1
} finally {await context.close();await browser.close()}
