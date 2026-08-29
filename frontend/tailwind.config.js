/** @type {import('tailwindcss').Config} */
/* 颜色统一由 src/index.css 的 :root 变量驱动（第六轮视觉 token 契约）。
   所有色值必须写成 rgb(var(--x) / <alpha-value>)：全站大量使用
   bg-accent/10、focus:border-accent/60 这类透明度修饰符，
   直接写 #RRGGBB 会让 /xx 后缀静默失效。 */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // 深色底板（ECharts 深色画布等），不随 token 变化
        ink: { 950: '#070A16', 900: '#0B1020', 850: '#0F1530', 800: '#141B3C' },
        // key 名保留（全站在用），值改为引用变量
        accent: {
          DEFAULT: 'rgb(var(--brand-accent) / <alpha-value>)',
          // 旧 key 名 cyan 全站在用，现指向强调渐变的紫端
          cyan: 'rgb(var(--brand-accent-2) / <alpha-value>)',
          violet: 'rgb(var(--brand-violet) / <alpha-value>)',
          deep: 'rgb(var(--brand-accent-deep) / <alpha-value>)',
        },
        // 旧键：曾经的近黑实心块，现已降级为标题蓝，避免再出现墨蓝大色块
        inkSolid: 'rgb(var(--ink-solid) / <alpha-value>)',
        brand: {
          ink: 'rgb(var(--brand-ink) / <alpha-value>)',
          inkSoft: 'rgb(var(--brand-ink-soft) / <alpha-value>)',
          accent: 'rgb(var(--brand-accent) / <alpha-value>)',
          accent2: 'rgb(var(--brand-accent-2) / <alpha-value>)',
          accent3: 'rgb(var(--brand-accent-3) / <alpha-value>)',
          accentDeep: 'rgb(var(--brand-accent-deep) / <alpha-value>)',
          violet: 'rgb(var(--brand-violet) / <alpha-value>)',
          bar1: 'rgb(var(--bar-1) / <alpha-value>)',
          bar2: 'rgb(var(--bar-2) / <alpha-value>)',
          bar3: 'rgb(var(--bar-3) / <alpha-value>)',
        },
        surface: {
          page: 'rgb(var(--surface-page) / <alpha-value>)',
          page2: 'rgb(var(--surface-page-2) / <alpha-value>)',
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
        // 语义色：危险 / 警示 / 成功，各带一个浅底
        danger: {
          DEFAULT: 'rgb(var(--danger) / <alpha-value>)',
          weak: 'rgb(var(--danger-weak) / <alpha-value>)',
        },
        warn: {
          DEFAULT: 'rgb(var(--warn) / <alpha-value>)',
          weak: 'rgb(var(--warn-weak) / <alpha-value>)',
        },
        success: {
          DEFAULT: 'rgb(var(--success) / <alpha-value>)',
          weak: 'rgb(var(--success-weak) / <alpha-value>)',
        },
      },
      /* 契约的卡片圆角是 20px，而全站统一写 rounded-2xl（默认 16px）。
         在这里把 2xl 抬到 20px，两条要求就不再打架，页面侧不用逐处改成任意值。 */
      borderRadius: { '2xl': '20px' },
      /* 契约推荐 border-line-soft/6、/18 这类刻度，默认 opacity scale 里没有，
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
        glow: '0 10px 28px rgb(var(--brand-accent) / 0.28)',
        card: '0 10px 30px -14px rgb(var(--brand-ink) / 0.16)',
        lift: '0 18px 44px -20px rgb(var(--brand-ink) / 0.24)',
      },
      backgroundImage: {
        // 主强调渐变：按钮、图标块、选中态。上面一律配白色前景。
        'grad-accent': 'linear-gradient(135deg, rgb(var(--brand-accent)) 0%, rgb(var(--brand-accent-2)) 100%)',
        // 进度条/仪表填充，与 grad-accent 同源
        'grad-fill': 'linear-gradient(135deg, rgb(var(--brand-accent)) 0%, rgb(var(--brand-accent-2)) 100%)',
        // 偏紫的一支，用于需要与主强调区分的次级图标块，同样配白色前景
        'grad-violet': 'linear-gradient(135deg, rgb(var(--brand-accent-2)) 0%, rgb(var(--bar-1)) 100%)',
        // 浅蓝白渐变：大面积面板/浅色选中态用，务必配深色字（浅底托不住白字）
        'grad-sky': 'linear-gradient(135deg, rgb(var(--brand-accent-3)) 0%, #FFFFFF 100%)',
        // 顶边饰条（.tg-topbar 用的是同一条，这里给需要内联的场景）
        'grad-topbar': 'linear-gradient(90deg, rgb(var(--bar-1)) 0%, rgb(var(--bar-2)) 52%, rgb(var(--bar-3)) 100%)',
        // 深色渐变：仅剩图表/深底徽标在用。原本这条声明了两次，后一条静默覆盖前一条，已合并。
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
