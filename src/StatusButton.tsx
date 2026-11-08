import type { Preview } from './api'
import type { Translate } from './messages'
import { Icon } from './Icon'

export function StatusButton({ preview, t, onClick, compact = false, name = '' }: { preview?: Preview; t: Translate; onClick: () => void; compact?: boolean; name?: string }) {
  const errors = preview?.issues.filter(issue => issue.severity === 'error').length ?? 0
  const warnings = preview?.issues.filter(issue => issue.severity === 'warning').length ?? 0
  const label = preview?.stale ? t('previewStale') : [
    errors ? t('errors', { count: errors }) : '',
    warnings ? t('warnings', { count: warnings }) : '',
  ].filter(Boolean).join(' · ') || t(preview ? 'ready' : 'checking')
  const state = preview?.stale ? 'warning' : errors ? 'error' : warnings ? 'warning' : preview ? 'success' : ''
  return <button className={`status-pill ${compact ? 'list-status' : ''} ${state}`} type="button" onClick={onClick}
    aria-label={t('checkPackage', { name, status: label })} title={label} aria-haspopup="dialog">
    <Icon name={errors ? 'error' : preview?.stale || warnings ? 'warning' : preview ? 'check' : 'clock'} />{!compact && <span>{label}</span>}
  </button>
}

