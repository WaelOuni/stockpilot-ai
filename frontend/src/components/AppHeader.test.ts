import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { expect, it } from 'vitest'
import AppHeader from './AppHeader.vue'

it('opens mobile navigation, closes with Escape and restores keyboard focus', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: ['/products', '/assistant', '/orders', '/admin/orders'].map(path => ({ path, component: { template: '<div />' } })) })
  await router.push('/products')
  const wrapper = mount(AppHeader, { attachTo: document.body, global: { plugins: [router] } })
  const button = wrapper.get('button')
  expect(button.attributes('aria-expanded')).toBe('false')
  await button.trigger('click')
  expect(wrapper.get('nav').classes()).toContain('open')
  await wrapper.get('header').trigger('keydown', { key: 'Escape' })
  expect(button.attributes('aria-expanded')).toBe('false')
  expect(document.activeElement).toBe(button.element)
  await button.trigger('click')
  await router.push('/assistant')
  expect(button.attributes('aria-expanded')).toBe('false')
  wrapper.unmount()
})
