import { expect, it } from 'vitest'
import { panelGeometry } from './PanelDivider'

it('clamps the displayed width while preserving room for the Explorer', () => {
  expect(panelGeometry(700, 745, 320)).toEqual({ width: 325, max: 325 })
  expect(panelGeometry(200, 1092, 350).width).toBe(300)
  expect(panelGeometry(700, 1092, 350).width).toBe(672)
  expect(panelGeometry(700, 1549, 370).width).toBe(700)
})

it('uses the responsive standard only when no preferred width is saved', () => {
  expect(panelGeometry(null, 745, 320).width).toBe(320)
  expect(panelGeometry(null, 1092, 350).width).toBe(350)
  expect(panelGeometry(null, 1549, 370).width).toBe(370)
  expect(panelGeometry(410, 1549, 370).width).toBe(410)
})
