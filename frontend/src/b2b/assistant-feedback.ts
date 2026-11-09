import { ref } from 'vue'
import type { AssistantTurn } from './assistant'

// One instance per mounted, authorized conversation. Nothing is persisted.
export function createResponseFeedback(soundEnabled: () => boolean, completed: () => void) {
  const waiting = ref(false), slow = ref(false), notice = ref('')
  const awaiting = new Set<string>(), notified = new Set<string>()
  let longWait: ReturnType<typeof setTimeout> | undefined
  let noticeTimer: ReturnType<typeof setTimeout> | undefined
  let audio: AudioContext | undefined
  function stopWaiting() { clearTimeout(longWait); waiting.value = false; slow.value = false }
  function silence() {
    const old = audio; audio = undefined
    try { void old?.close().catch(() => {}) } catch { /* Optional browser API. */ }
  }
  function unlockSound() {
    if (!soundEnabled() || typeof AudioContext === 'undefined') return
    try {
      audio ??= new AudioContext()
      void audio.resume().catch(() => {})
    } catch { silence() }
  }
  function chime() {
    if (!soundEnabled() || !audio || audio.state !== 'running') return
    try {
      const tone = audio.createOscillator(), gain = audio.createGain(), now = audio.currentTime
      tone.type = 'sine'; tone.frequency.setValueAtTime(660, now)
      gain.gain.setValueAtTime(0, now)
      gain.gain.linearRampToValueAtTime(0.035, now + 0.015)
      gain.gain.linearRampToValueAtTime(0, now + 0.18)
      tone.connect(gain); gain.connect(audio.destination)
      tone.onended = () => { tone.disconnect(); gain.disconnect() }
      tone.start(now); tone.stop(now + 0.19)
    } catch { /* Visual completion is independent of audio support/autoplay. */ }
  }
  function remember(turn: AssistantTurn) {
    if (turn.kind === 'message' && turn.status === 'pending') awaiting.add(turn.turn_id)
  }
  function start(turnId: string) {
    stopWaiting(); clearTimeout(noticeTimer); notice.value = ''
    awaiting.add(turnId); waiting.value = true
    longWait = setTimeout(() => { slow.value = true }, 10000)
  }
  function accept(turn: AssistantTurn) {
    if (turn.status !== 'completed' || turn.kind !== 'message' || !turn.reply ||
        !awaiting.has(turn.turn_id) || notified.has(turn.turn_id)) return false
    awaiting.delete(turn.turn_id); notified.add(turn.turn_id)
    stopWaiting(); clearTimeout(noticeTimer)
    const reply = turn.reply
    const usefulRead = ['PRODUCT_ADVICE', 'LOW_STOCK', 'STOCK_CHECK', 'COMPARISON'].includes(reply.intent) && reply.products.length > 0 && !reply.order
    notice.value = reply.intent === 'ORDER_PREPARE' ? 'Préparation à vérifier' : usefulRead ? 'Réponse prête' : 'Réponse reçue'
    noticeTimer = setTimeout(() => { notice.value = '' }, 4000)
    if (usefulRead) chime()
    completed()
    return true
  }
  function reset() {
    stopWaiting(); clearTimeout(noticeTimer); notice.value = ''
    awaiting.clear(); notified.clear(); silence()
  }
  return { waiting, slow, notice, start, stopWaiting, remember, accept, reset, silence, unlockSound }
}
