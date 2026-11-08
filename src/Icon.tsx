import type { ReactNode } from 'react'

const shapes = {
  group: <><rect x="3" y="3" width="13" height="13" rx="2" /><path d="M8 20h11a2 2 0 0 0 2-2V8M7 8h5M7 12h5" /></>,
  ungroup: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M8 8h8v8H8z" />,
  grip: <>{[5,12,19].flatMap(y => [9,15].map(x => <circle key={`${x}-${y}`} cx={x} cy={y} r=".7" />))}</>,
  more: <><circle cx="5" cy="12" r=".8" /><circle cx="12" cy="12" r=".8" /><circle cx="19" cy="12" r=".8" /></>,
  undo: <path d="M4 4v7h7M4 11c2-7 15-7 16 2 1 5-4 8-8 7" />,
  up: <path d="m6 15 6-6 6 6" />,
  cut: <><circle cx="6" cy="6" r="3" /><circle cx="6" cy="18" r="3" /><path d="m8 8 12 12M8 16 20 4" /></>,
  copy: <><rect x="8" y="8" width="12" height="13" rx="1" /><path d="M16 8V3H3v13h5" /></>,
  paste: <><path d="M9 5H5v16h14V5h-4" /><rect x="9" y="3" width="6" height="4" rx="1" /></>,
  rename: <><path d="M4 8h7M4 16h7M16 4v16m-3-16h6m-6 16h6" /></>,
  settings: <><path d="m10 3-.5 2-2 1-2-.5L3 9l1.5 1.5v3L3 15l2.5 3.5 2-.5 2 1 .5 2h4l.5-2 2-1 2 .5L21 15l-1.5-1.5v-3L21 9l-2.5-3.5-2 .5-2-1L14 3h-4Z" /><circle cx="12" cy="12" r="3" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  play: <path d="m8 5 11 7-11 7V5Z" />,
  error: <><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6m0-6-6 6" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  chevron: <path d="m9 5 7 7-7 7" />,
  down: <path d="m6 9 6 6 6-6" />,
  folder: <path d="M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z" />,
  folderopen: <path d="M3 10V5h6l2 3h9v3M3 10h18l-3 9H5l-2-9Z" />,
  file: <><path d="M5 3h9l5 5v13H5V3Z" /><path d="M14 3v6h5" /></>,
  image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8" cy="8" r="1.5" /><path d="m3 17 6-6 5 5 3-3 4 4" /></>,
  external: <path d="M14 3h7v7m0-7L10 14M10 3H4v17h17v-6" />,
  warning: <><path d="m12 3 10 17H2L12 3Z" /><path d="M12 9v4m0 3h.01" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10h.01" /></>,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  appearance: <><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4M12 4v13"/></>,
  pin: <path d="M9 3h6l-1 7 4 4v2H6v-2l4-4-1-7Zm3 13v6" />,
  pinoff: <><path d="M9 3h6l-1 7 1 1M7 13l-1 1v2h10m-4 0v6" /><path d="m3 3 18 18" /></>,
  trash: <path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7" />,
  refresh: <path d="M20 10a8 8 0 0 0-14-5L3 8m0-5v5h5M4 14a8 8 0 0 0 14 5l3-3m0 5v-5h-5" />,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  eye: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></>,
  eyeoff: <path d="m3 3 18 18M10.5 5.2C16.6 4.4 22 12 22 12a23 23 0 0 1-4 4M6 6a23 23 0 0 0-4 6s4 7 10 7a11 11 0 0 0 5-1.4M10 10a3 3 0 0 0 4 4" />,
  arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
  archive: <><rect x="3" y="3" width="18" height="5" rx="1" /><path d="M5 8v13h14V8M10 12h4" /></>,
  list: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="M3 6h2M3 12h2M3 18h2" /></>,
} satisfies Record<string, ReactNode>

export type IconName = keyof typeof shapes

export function Icon({ name, className = '' }: { name: IconName; className?: string }) {
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true">{shapes[name]}</svg>
}
