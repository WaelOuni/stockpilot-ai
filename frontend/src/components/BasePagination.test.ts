import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Pagination from './BasePagination.vue'

describe('exact pagination', () => {
  it('shows exact totals and reaches first / last / numbered pages', async () => {
    const wrapper = mount(Pagination, { props: { offset: 20, limit: 20, count: 20, total: 101, hasMore: true, label: 'produits' } })
    expect(wrapper.text()).toContain('Affichage de 21 à 40 sur 101 produits')
    expect(wrapper.text()).toContain('Page 2 sur 6')
    const button = (name: string) => wrapper.findAll('button').find(item => item.text() === name)!
    await button('Première page').trigger('click')
    await button('Dernière page').trigger('click')
    await wrapper.get('[aria-label="Page 3"]').trigger('click')
    expect(wrapper.emitted('change')).toEqual([[0], [100], [40]])
    expect(wrapper.get('[aria-current=page]').text()).toBe('2')
  })
  it('disables bounds for first and final pages including exact multiples', async () => {
    const wrapper = mount(Pagination, { props: { offset: 0, count: 20, total: 40, hasMore: true } })
    expect(wrapper.findAll('button').find(b => b.text() === 'Première page')!.attributes('disabled')).toBeDefined()
    await wrapper.setProps({ offset: 20, hasMore: false })
    for (const name of ['Suivant', 'Dernière page']) expect(wrapper.findAll('button').find(b => b.text() === name)!.attributes('disabled')).toBeDefined()
  })
  it('shows zero results without invented pages', () => {
    const wrapper = mount(Pagination, { props: { offset: 0, count: 0, total: 0, hasMore: false } })
    expect(wrapper.text()).toContain('Affichage de 0 à 0 sur 0 résultats')
    expect(wrapper.text()).toContain('Page 0 sur 0')
    expect(wrapper.findAll('button').every(button => button.attributes('disabled') !== undefined)).toBe(true)
  })
  it('preserves old has_more clients without manufacturing totals', () => {
    const wrapper = mount(Pagination, { props: { offset: 0, count: 20, hasMore: false } })
    expect(wrapper.text()).not.toContain('sur')
    expect(wrapper.findAll('button').find(b => b.text() === 'Suivant')!.attributes('disabled')).toBeDefined()
  })
  it('blocks every control while loading', () => {
    const wrapper = mount(Pagination, { props: { offset: 20, count: 20, total: 101, hasMore: true, loading: true } })
    expect(wrapper.findAll('button').every(button => button.attributes('disabled') !== undefined)).toBe(true)
  })
})
