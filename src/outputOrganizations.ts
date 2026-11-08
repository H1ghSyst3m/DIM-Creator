import type { OutputOrganization } from './api'
import type { Message, Translate } from './messages'

export const outputOrganizations = {
  flat: 'outputFlat',
  byDate: 'outputByDate',
} as const satisfies Record<OutputOrganization, Message>

export function outputOrganizationOptions(t: Translate) {
  return Object.entries(outputOrganizations).map(([value, label]) => ({value, label:t(label)}))
}
