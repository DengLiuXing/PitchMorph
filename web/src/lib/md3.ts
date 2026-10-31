/**
 * canvas 需要的具体色值从 MD3 角色变量里读，避免在 JS 里再抄一份色板。
 * 读不到时（首帧 CSS 尚未应用）退回与 index.css 一致的字面量。
 */
const colorCache = new Map<string, string>()

export function roleColor(token: string, fallback: string): string {
  const cached = colorCache.get(token)
  if (cached) return cached
  const value = getComputedStyle(document.documentElement).getPropertyValue(token).trim()
  const resolved = value || fallback
  colorCache.set(token, resolved)
  return resolved
}

function withAlpha(color: string, alpha: number): string {
  const hex = color.replace('#', '')
  if (hex.length !== 6) return color
  const red = parseInt(hex.slice(0, 2), 16)
  const green = parseInt(hex.slice(2, 4), 16)
  const blue = parseInt(hex.slice(4, 6), 16)
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`
}

/** 每条轨的色相：人声=primary、鼓=tertiary、贝斯=secondary、其他=on-surface-variant */
const STEM_ROLES: Record<string, [string, string]> = {
  vocals: ['--md-primary', '#FFB95C'],
  drums: ['--md-tertiary', '#7FD8CB'],
  bass: ['--md-secondary', '#DCC3A5'],
  other: ['--md-on-surface-variant', '#D5C4B4'],
  source: ['--md-on-surface-variant', '#D5C4B4'],
}

export function stemColor(name: string): string {
  const [token, fallback] = STEM_ROLES[name] ?? STEM_ROLES.other
  return roleColor(token, fallback)
}

export interface CanvasPalette {
  grid: string
  gridMinor: string
  axis: string
  label: string
  labelDim: string
  shade: string
  cursor: string
  fontLarge: string
  fontSmall: string
}

let cachedPalette: CanvasPalette | null = null

/** 网格、刻度、播放头这些中性色都从 on-surface-variant / primary 派生 */
export function canvasPalette(): CanvasPalette {
  if (cachedPalette) return cachedPalette
  const neutral = roleColor('--md-on-surface-variant', '#D5C4B4')
  const primary = roleColor('--md-primary', '#FFB95C')
  const lowest = roleColor('--md-surface-container-lowest', '#100D09')
  cachedPalette = {
    grid: withAlpha(neutral, 0.07),
    gridMinor: withAlpha(neutral, 0.045),
    axis: withAlpha(neutral, 0.12),
    label: withAlpha(neutral, 0.62),
    labelDim: withAlpha(neutral, 0.42),
    shade: withAlpha(lowest, 0.8),
    cursor: withAlpha(primary, 0.6),
    fontLarge: '10px "Roboto Mono", "Cascadia Mono", ui-monospace, Consolas, monospace',
    fontSmall: '9px "Roboto Mono", "Cascadia Mono", ui-monospace, Consolas, monospace',
  }
  return cachedPalette
}
