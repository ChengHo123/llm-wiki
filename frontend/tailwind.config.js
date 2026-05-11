/** @type {import('tailwindcss').Config} */
import typography from '@tailwindcss/typography'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      typography: {
        DEFAULT: {
          css: {
            // 中英文混排調校：留空隔、加大行高、控住粗體濫用感
            lineHeight: '1.75',
            fontFamily: '"PingFang TC", "Microsoft JhengHei", "Noto Sans TC", "Source Han Sans TC", system-ui, -apple-system, "Segoe UI", sans-serif',
            'h1, h2, h3, h4': { lineHeight: '1.35', marginTop: '1.5em', marginBottom: '0.6em' },
            p: { marginTop: '0.75em', marginBottom: '0.75em' },
            strong: { fontWeight: '600' },
            'ul, ol': { marginTop: '0.6em', marginBottom: '0.6em' },
            li: { marginTop: '0.25em', marginBottom: '0.25em' },
          },
        },
      },
    },
  },
  plugins: [typography],
}
