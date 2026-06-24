import { useEffect, useRef, useState, ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, Check } from 'lucide-react'

type Opt = string | { value: string; label: string }

function norm(o: Opt) { return typeof o === 'string' ? { value: o, label: o } : o }

export default function Select({ value, onChange, options, placeholder = '请选择', className = '', icon, align = 'left' }: {
  value: string | number | null
  onChange: (v: string) => void
  options: Opt[]
  placeholder?: string
  className?: string
  icon?: ReactNode
  align?: 'left' | 'right'
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const opts = options.map(norm)
  const current = opts.find(o => String(o.value) === String(value))

  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  return (
    <div ref={ref} className={`relative ${className}`}>
      <button type="button" onClick={() => setOpen(o => !o)}
        className={`w-full flex items-center gap-2 rounded-xl bg-white/80 border px-3.5 py-2.5 text-sm text-slate-700 transition outline-none
          ${open ? 'border-accent/60 ring-2 ring-accent/15 bg-white' : 'border-slate-200 hover:border-sky-300'}`}>
        {icon && <span className="text-slate-400 shrink-0">{icon}</span>}
        <span className={`flex-1 text-left truncate ${current ? '' : 'text-slate-400'}`}>{current?.label ?? placeholder}</span>
        <ChevronDown className={`w-4 h-4 text-slate-400 shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }} transition={{ duration: 0.16 }}
            className={`absolute z-30 mt-2 min-w-full max-h-64 overflow-auto rounded-2xl bg-white border border-slate-200 shadow-xl p-1.5 ${align === 'right' ? 'right-0' : 'left-0'}`}
            style={{ boxShadow: '0 16px 40px -12px rgba(37,99,235,0.25)' }}>
            {opts.map(o => {
              const active = String(o.value) === String(value)
              return (
                <button key={o.value} type="button"
                  onClick={() => { onChange(o.value); setOpen(false) }}
                  className={`w-full flex items-center justify-between gap-2 rounded-xl px-3 py-2 text-sm text-left transition
                    ${active ? 'bg-grad-accent text-white font-semibold' : 'text-slate-600 hover:bg-sky-50'}`}>
                  <span className="truncate">{o.label}</span>
                  {active && <Check className="w-4 h-4 shrink-0" />}
                </button>
              )
            })}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
