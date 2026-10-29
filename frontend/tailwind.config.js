/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        // 与 v1 的 :root 变量一一对应，改这里要同步 index.css
        ink: {
          900: '#0A0C10',
          800: '#12161F',
          700: '#1A2030',
        },
        amber: {
          DEFAULT: '#F2A93B',
          bright: '#FFC46B',
        },
        teal: {
          DEFAULT: '#2DD4BF',
        },
        chalk: {
          DEFAULT: '#E8ECF1',
          dim: '#8A94A6',
          mute: '#5A6373',
        },
      },
      fontFamily: {
        sans: ['PingFang SC', 'Microsoft YaHei', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['Cascadia Mono', 'JetBrains Mono', 'ui-monospace', 'Consolas', 'monospace'],
      },
      boxShadow: {
        hairline: 'inset 0 0 0 1px rgba(255,255,255,0.06)',
        glow: '0 0 40px -12px rgba(240,169,22,0.35)',
      },
      keyframes: {
        rise: {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        pulseRing: {
          '0%, 100%': { opacity: '0.35', transform: 'scale(1)' },
          '50%': { opacity: '0.8', transform: 'scale(1.6)' },
        },
      },
      animation: {
        rise: 'rise 0.32s cubic-bezier(0.22, 1, 0.36, 1) both',
        pulseRing: 'pulseRing 2.4s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
