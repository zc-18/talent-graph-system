/** @type {import('tailwindcss').Config} */
/* 颜色统一由 src/index.css 的 :root 变量驱动（第五轮视觉 token 契约）。
   所有色值必须写成 rgb(var(--x) / <alpha-value>)：全站大量使用
   bg-accent/10、focus:border-accent/60 这类透明度修饰符，
   直接写 #RRGGBB 会让 /xx 后缀静默失效。 */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // 深色底板（图表/深色块用），不随 token 变化
        ink: { 950: '#070A16', 900: '#0B1020', 850: '#0F1530', 800: '#141B3C' },
        // key 名保留（全站在用），值改为引用变量
        accent: {
          DEFAULT: 'rgb(var(--brand-accent) / <alpha-value>)',
          // 旧 key 名 cyan 全站在用，值已指向天蓝浅端，不再是青色
          cyan: 'rgb(var(--brand-accent-2) / <alpha-value>)',
          violet: 'rgb(var(--brand-violet) / <alpha-value>)',
          deep: 'rgb(var(--brand-accent-deep) / <alpha-value>)',
        },
        // 中性近黑：主按钮 / 侧栏选中项。刻意不用深蓝——深蓝大色块会被看成带绿。
        inkSolid: 'rgb(var(--ink-solid) / <alpha-value>)',
        brand: {
          ink: 'rgb(var(--brand-ink) / <alpha-value>)',
          inkSoft: 'rgb(var(--brand-ink-soft) / <alpha-value>)',
          accent: 'rgb(var(--brand-accent) / <alpha-value>)',
          accent2: 'rgb(var(--brand-accent-2) / <alpha-value>)',
          accent3: 'rgb(var(--brand-accent-3) / <alpha-value>)',
          accentDeep: 'rgb(var(--brand-accent-deep) / <alpha-value>)',
          violet: 'rgb(var(--brand-violet) / <alpha-value>)',
        },
        surface: {
          page: 'rgb(var(--surface-page) / <alpha-value>)',
          card: 'rgb(var(--surface-card) / <alpha-value>)',
          muted: 'rgb(var(--surface-muted) / <alpha-value>)',
        },
        line: {
          soft: 'rgb(var(--line-soft) / <alpha-value>)',
          strong: 'rgb(var(--line-strong) / <alpha-value>)',
        },
        body: {
          1: 'rgb(var(--text-1) / <alpha-value>)',
          2: 'rgb(var(--text-2) / <alpha-value>)',
          3: 'rgb(var(--text-3) / <alpha-value>)',
        },
      },
      /* 契约推荐 border-line-soft/9、/18 这类刻度，默认 opacity scale 里没有，
         不补齐会让 @apply 直接报 "class does not exist"。只加刻度，不改任何 token 值。 */
      opacity: {
        4: '0.04', 6: '0.06', 8: '0.08', 9: '0.09', 12: '0.12', 14: '0.14',
        15: '0.15', 18: '0.18', 22: '0.22', 26: '0.26', 35: '0.35', 45: '0.45',
        55: '0.55', 65: '0.65', 85: '0.85',
      },
      fontFamily: {
        sans: ['"Noto Sans SC"', '"Microsoft YaHei"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        glow: '0 6px 18px -8px rgb(var(--brand-accent) / 0.20)',
        card: '0 10px 30px -12px rgb(var(--brand-ink) / 0.18)',
        lift: '0 18px 44px -20px rgb(var(--brand-ink) / 0.28)',
      },
      backgroundImage: {
        'grad-accent': 'linear-gradient(135deg, rgb(var(--brand-accent-2)) 0%, rgb(var(--brand-accent-3)) 58%, #FFFFFF 100%)',
        // 进度条/仪表填充：浅蓝白渐变在白轨道上等于隐形，这里保留有饱和度的一支
        'grad-fill': 'linear-gradient(135deg, rgb(var(--brand-accent)) 0%, rgb(var(--brand-accent-deep)) 100%)',
        'grad-violet': 'linear-gradient(135deg, rgb(var(--brand-violet)) 0%, rgb(var(--brand-accent-deep)) 100%)',
        // 蓝白渐变：大面积面板/选中态用，务必配深色字（浅底托不住白字）
        'grad-sky': 'linear-gradient(135deg, rgb(var(--brand-accent-3)) 0%, rgb(var(--brand-accent-2) / .55) 45%, #FFFFFF 100%)',
        'grad-ink': 'linear-gradient(135deg, rgb(var(--ink-solid)) 0%, rgb(var(--brand-ink)) 100%)',
        'grad-ink': 'linear-gradient(135deg, rgb(var(--brand-ink)) 0%, rgb(var(--brand-ink-soft)) 100%)',
      },
      keyframes: {
        float: { '0%,100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-6px)' } },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: { float: 'float 6s ease-in-out infinite' },
    },
  },
  plugins: [],
}
