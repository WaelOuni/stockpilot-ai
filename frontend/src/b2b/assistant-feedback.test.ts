import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createResponseFeedback } from './assistant-feedback'
import type { AssistantTurn } from './assistant'

const turn: AssistantTurn = {
  turn_id: 'new', kind: 'message', message: 'question', status: 'completed', created_at: '2026-11-09T10:00:00Z', operation: null,
  reply: { intent: 'PRODUCT_ADVICE', text: 'Verified catalogue', can_confirm: false, can_browse_products: false, order: null,
    products: [{ product_id: 'p', organization_id: 'org-a', sku: 'SKU', name: 'Drill', price_minor: '9999', currency: 'EUR', quantity: 5, reserved: 1, available: 4, revision: 1, observed_at: '2026-11-09T10:00:00Z' }] },
}
beforeEach(() => vi.useFakeTimers())
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

function audio(state = 'running') {
  const oscillator = { type: '', frequency: { setValueAtTime: vi.fn() }, connect: vi.fn(), disconnect: vi.fn(), start: vi.fn(), stop: vi.fn(), onended: null }
  const context = { state, currentTime: 0, destination: {}, resume: vi.fn(async () => {}), close: vi.fn(async () => {}), createOscillator: vi.fn(() => oscillator), createGain: vi.fn(() => ({ gain: { setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn() }, connect: vi.fn(), disconnect: vi.fn() })) }
  vi.stubGlobal('AudioContext', vi.fn(function () { return context }))
  return { context, oscillator }
}

it('UXP-01/02/04/05: immediate indeterminate waiting, real long wait, unique neutral notice', () => {
  const completed = vi.fn(), f = createResponseFeedback(() => false, completed)
  expect([f.waiting.value, f.slow.value, f.notice.value]).toEqual([false, false, ''])
  f.start('new')
  expect(f.waiting.value).toBe(true)
  vi.advanceTimersByTime(9999); expect(f.slow.value).toBe(false)
  vi.advanceTimersByTime(1); expect(f.slow.value).toBe(true)
  expect(f.accept(turn)).toBe(true)
  expect(f.waiting.value).toBe(false); expect(f.slow.value).toBe(false)
  expect(f.notice.value).toBe('Réponse prête')
  expect(f.accept(turn)).toBe(false); expect(completed).toHaveBeenCalledTimes(1)
  vi.advanceTimersByTime(4000); expect(f.notice.value).toBe('')
  expect(vi.getTimerCount()).toBe(0)
})

it('UXP-03: a fast reply has no artificial delay', () => {
  const f = createResponseFeedback(() => false, vi.fn()); f.start('new')
  expect(f.accept(turn)).toBe(true)
  expect(f.waiting.value).toBe(false)
  vi.advanceTimersByTime(12000); expect(f.slow.value).toBe(false); f.reset()
})

it.each(['NO_RESULTS', 'PRODUCT_ADVICE', 'CLARIFICATION_REQUIRED', 'ORDER_REFUSED', 'ORDER_PREPARE', 'RAG'])('UXP-06/07/08: empty %s never plays a success sound', intent => {
  const { context } = audio(), f = createResponseFeedback(() => true, vi.fn())
  f.unlockSound(); f.start('new'); f.accept({ ...turn, reply: { ...turn.reply!, intent, products: [] } })
  expect(context.createOscillator).not.toHaveBeenCalled()
  expect(f.notice.value).toBe(intent === 'ORDER_PREPARE' ? 'Préparation à vérifier' : 'Réponse reçue')
  f.reset()
})

it('UXP-09/10/11/12: error/pending is not completion; resume notifies once, historical turns never notify', () => {
  const notify = vi.fn(), f = createResponseFeedback(() => false, notify)
  expect(f.accept(turn)).toBe(false)
  f.start('new'); f.stopWaiting()
  expect(f.notice.value).toBe('')
  f.remember({ ...turn, status: 'pending', reply: null })
  expect(f.accept({ ...turn, status: 'pending', reply: null })).toBe(false)
  expect(f.accept(turn)).toBe(true)
  f.start('new'); f.stopWaiting(); expect(f.accept(turn)).toBe(false)
  expect(notify).toHaveBeenCalledTimes(1); f.reset()
})

it('UXP-14/15/16/24: reset drops late responses and closes every audio/timer resource', () => {
  const { context } = audio(), notify = vi.fn(), f = createResponseFeedback(() => true, notify)
  f.unlockSound(); f.start('new'); f.reset()
  expect(f.accept(turn)).toBe(false); expect(notify).not.toHaveBeenCalled()
  expect(context.close).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0)
})

it('UXP-17: OFF creates no audio context and still displays completion', () => {
  audio(); const f = createResponseFeedback(() => false, vi.fn())
  f.unlockSound(); f.start('new'); f.accept(turn)
  expect(AudioContext).not.toHaveBeenCalled(); expect(f.notice.value).toBe('Réponse prête'); f.reset()
})

it('UXP-18/20/24: explicit gesture unlocks a single soft sound below 250ms, OFF is reversible', () => {
  const { context, oscillator } = audio(); let enabled = true
  const f = createResponseFeedback(() => enabled, vi.fn())
  f.unlockSound(); f.start('new'); f.accept(turn); f.accept(turn)
  expect(context.resume).toHaveBeenCalledTimes(1); expect(oscillator.start).toHaveBeenCalledTimes(1)
  expect(oscillator.stop).toHaveBeenCalledWith(0.19)
  enabled = false; f.silence(); f.start('other'); f.accept({ ...turn, turn_id: 'other' })
  expect(oscillator.start).toHaveBeenCalledTimes(1); expect(context.close).toHaveBeenCalledTimes(1); f.reset()
})

it.each(['missing', 'blocked', 'throws'])('UXP-19: %s audio leaves visual feedback working', mode => {
  const { context } = audio(mode === 'blocked' ? 'suspended' : 'running')
  if (mode === 'missing') vi.stubGlobal('AudioContext', undefined)
  if (mode === 'throws') vi.stubGlobal('AudioContext', vi.fn(function () { throw new Error('unsupported') }))
  if (mode === 'blocked') context.resume.mockRejectedValue(new Error('autoplay'))
  const f = createResponseFeedback(() => true, vi.fn()); f.unlockSound(); f.start('new'); f.accept(turn)
  expect(f.notice.value).toBe('Réponse prête'); expect(context.createOscillator).not.toHaveBeenCalled(); f.reset()
})
