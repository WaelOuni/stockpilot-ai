import { mount } from '@vue/test-utils'
import { afterEach, expect, it } from 'vitest'
import CustomerTokenPanel from './CustomerTokenPanel.vue'
import { customerToken, setCustomerToken } from '../utils/customerSession'

afterEach(() => { setCustomerToken('') })

it('accepts a token in memory, clears the input and disconnects without persistence', async () => {
  const wrapper = mount(CustomerTokenPanel)
  await wrapper.get('input').setValue(' test-only-token ')
  await wrapper.get('form').trigger('submit')
  expect(customerToken.value).toBe('test-only-token')
  expect((wrapper.get('input').element as HTMLInputElement).value).toBe('')
  expect(wrapper.text()).not.toContain('test-only-token')
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
  await wrapper.get('button.secondary').trigger('click')
  expect(customerToken.value).toBe('')
  wrapper.unmount()
})
