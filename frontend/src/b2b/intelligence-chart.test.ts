import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import IntelligenceChart from './IntelligenceChart.vue'
import { language } from './preferences'

describe('Intelligence chart numeric and missing-data boundaries', () => {
  it.each(['fr', 'en'] as const)('announces a local range failure without hiding valid points in %s', locale => {
    language.value = locale
    const wrapper = mount(IntelligenceChart, { props: { title: 'Observed data', unit: 'units', labels: ['1', '2', '3'], values: [2, Number.MAX_VALUE, 3] } })
    expect(wrapper.text()).toContain(locale === 'fr' ? 'Hors plage de visualisation' : 'Outside visualization range')
    expect(wrapper.find('svg').exists()).toBe(true)
    expect(wrapper.html()).not.toMatch(/NaN|Infinity/)
    expect(wrapper.findAll('path')).toHaveLength(2)
    wrapper.unmount()
  })
  it('keeps an isolated observed day visible and preserves the missing-day gap', () => {
    const wrapper = mount(IntelligenceChart, { props: { title: 'Observed data', unit: 'units', labels: ['1', '2', '3'], values: [null, 7, null] } })
    expect(wrapper.find('svg[role="img"]').attributes('aria-label')).toContain('Observed data')
    expect(wrapper.findAll('circle')).toHaveLength(1)
    wrapper.unmount()
  })
  it('renders no chart when every day is unknown', () => {
    const wrapper = mount(IntelligenceChart, { props: { title: 'DEMO', unit: 'units', labels: ['1', '2'], values: [null, null] } })
    expect(wrapper.find('svg').exists()).toBe(false)
    wrapper.unmount()
  })
})
