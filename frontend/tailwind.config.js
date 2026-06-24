/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 950: '#070A16', 900: '#0B1020', 850: '#0F1530', 800: '#141B3C' },
        accent: { DEFAULT: '#6366F1', cyan: '#22D3EE', violet: '#A855F7', emerald: '#34D399' },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '"Noto Sans SC"', '"Microsoft YaHei"', 'sans-serif'],
      },
      boxShadow: {
        glow: '0 8px 24px -6px rgba(59,130,246,0.40)',
        card: '0 10px 30px -12px rgba(37,99,235,0.18)',
      },
      backgroundImage: {
        'grad-accent': 'linear-gradient(135deg, #6366F1 0%, #22D3EE 100%)',
        'grad-violet': 'linear-gradient(135deg, #A855F7 0%, #6366F1 100%)',
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
