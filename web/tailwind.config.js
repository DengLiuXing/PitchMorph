/**
 * MD3 的 color role 映射成 Tailwind 名字：组件里只写角色（bg-surface-container /
 * text-on-surface-variant），不写死色值。色值本体在 src/index.css 的 :root。
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        primary: 'var(--md-primary)',
        'on-primary': 'var(--md-on-primary)',
        'primary-container': 'var(--md-primary-container)',
        'on-primary-container': 'var(--md-on-primary-container)',
        secondary: 'var(--md-secondary)',
        'on-secondary': 'var(--md-on-secondary)',
        'secondary-container': 'var(--md-secondary-container)',
        'on-secondary-container': 'var(--md-on-secondary-container)',
        tertiary: 'var(--md-tertiary)',
        'on-tertiary': 'var(--md-on-tertiary)',
        'tertiary-container': 'var(--md-tertiary-container)',
        'on-tertiary-container': 'var(--md-on-tertiary-container)',
        surface: 'var(--md-surface)',
        'surface-lowest': 'var(--md-surface-container-lowest)',
        'surface-low': 'var(--md-surface-container-low)',
        'surface-container': 'var(--md-surface-container)',
        'surface-high': 'var(--md-surface-container-high)',
        'surface-highest': 'var(--md-surface-container-highest)',
        'on-surface': 'var(--md-on-surface)',
        'on-surface-variant': 'var(--md-on-surface-variant)',
        outline: 'var(--md-outline)',
        'outline-variant': 'var(--md-outline-variant)',
        error: 'var(--md-error)',
        'on-error': 'var(--md-on-error)',
        'error-container': 'var(--md-error-container)',
        'on-error-container': 'var(--md-on-error-container)',
        'inverse-surface': 'var(--md-inverse-surface)',
        'inverse-on-surface': 'var(--md-inverse-on-surface)',
        'inverse-primary': 'var(--md-inverse-primary)',
      },
      fontFamily: {
        sans: ['Roboto', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'],
        mono: ['Roboto Mono', 'Cascadia Mono', 'ui-monospace', 'Consolas', 'monospace'],
      },
      borderRadius: {
        'md-xs': '4px',
        'md-sm': '8px',
        'md-md': '12px',
        'md-lg': '16px',
        'md-xl': '28px',
      },
      boxShadow: {
        'md-1': '0 1px 2px rgba(0,0,0,0.32), 0 1px 3px 1px rgba(0,0,0,0.16)',
        'md-2': '0 1px 2px rgba(0,0,0,0.32), 0 2px 6px 2px rgba(0,0,0,0.18)',
        'md-3': '0 4px 8px 3px rgba(0,0,0,0.18), 0 1px 3px rgba(0,0,0,0.32)',
      },
      transitionTimingFunction: {
        'md-standard': 'cubic-bezier(0.2, 0, 0, 1)',
        'md-decelerate': 'cubic-bezier(0.05, 0.7, 0.1, 1)',
        'md-accelerate': 'cubic-bezier(0.3, 0, 0.8, 0.15)',
      },
      keyframes: {
        'sheet-in': {
          '0%': { opacity: '0', transform: 'translateY(24px) scale(0.98)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        'menu-in': {
          '0%': { opacity: '0', transform: 'scale(0.94)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'fade-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'rise-in': {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-track': {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(300%)' },
        },
      },
      animation: {
        'sheet-in': 'sheet-in 280ms cubic-bezier(0.05, 0.7, 0.1, 1) both',
        'menu-in': 'menu-in 120ms cubic-bezier(0.2, 0, 0, 1) both',
        'fade-in': 'fade-in 200ms cubic-bezier(0.2, 0, 0, 1) both',
        'rise-in': 'rise-in 260ms cubic-bezier(0.05, 0.7, 0.1, 1) both',
        'pulse-track': 'pulse-track 1.6s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
}
