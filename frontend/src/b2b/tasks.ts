import { ref } from 'vue'
import { StaleResponse } from './client'
export function useTask() {
  const busy = ref(false)
  const error = ref('')
  async function run(work: () => Promise<void>) {
    if (busy.value) return
    busy.value = true; error.value = ''
    try { await work() }
    catch (e) { if (!(e instanceof StaleResponse)) error.value = e instanceof Error ? e.message : 'Action impossible.' }
    finally { busy.value = false }
  }
  return { busy, error, run }
}
