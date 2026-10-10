import { readonly, ref } from 'vue'

const token = ref('')
export const customerToken = readonly(token)

export function setCustomerToken(value: string): void {
  token.value = value.trim()
}
