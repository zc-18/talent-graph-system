import { useEffect, useRef, useState, ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, Check } from 'lucide-react'

type Opt = string | { value: string; label: string }

function norm(o: Opt) { return typeof o === 'string' ? { value: o, label: o } : o }

export default function Select({ value, onChange, options, placeholder = '请选择', className = '', icon, align = 'left', label }: {
  value: string | number | null
  onChange: (v: string) => void
  options: Opt[]
  placeholder?: string
  className?: string
  icon?: ReactNode
  align?: 'left' | 'right'
  label?: string
}) {
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(-1) // 键盘高亮项
  const ref = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const opts = options.map(norm)
  const current = opts.find(o => String(o.value) === String(value))

  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  // 打开时高亮当前项并滚动到可见
  useEffect(() => {
    if (open) {
      const idx = opts.findIndex(o => String(o.value) === String(value))
      setHi(idx >= 0 ? idx : 0)
      requestAnimationFrame(() => {
        listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
      })
    }
  }, [open])

  const commit = (v: string) => { onChange(v); setOpen(false); btnRef.current?.focus() }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); setOpen(true) }
      return
    }
    if (e.key === 'Escape') { e.preventDefault(); setOpen(false); btnRef.current?.focus() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setHi(i => Math.min(opts.length - 1, i + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi(i => Math.max(0, i - 1)) }
    else if (e.key === 'Home') { e.preventDefault(); setHi(0) }
    else if (e.key === 'End') { e.preventDefault(); setHi(opts.length - 1) }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (hi >= 0) commit(opts[hi].value) }
    else if (e.key === 'Tab') setOpen(false)
  }

  useEffect(() => {
    if (open && hi >= 0) listRef.current?.children[hi]?.scrollIntoView({ block: 'nearest' })
  }, [hi, open])

  return (
    <div ref={ref} className={`relative ${className}`} onKeyDown={onKeyDown}>
      <button ref={btnRef} type="button" onClick={() => setOpen(o => !o)}
        aria-haspopup="listbox" aria-expanded={open} aria-label={label || placeholder}
        className={`w-full flex items-center gap-2 rounded-xl bg-white/80 border px-3.5 py-2.5 text-sm text-slate-700 transition outline-none
          focus-visible:ring-2 focus-visible:ring-accent/30
          ${open ? 'border-accent/60 ring-2 ring-accent/15 bg-white' : 'border-slate-200 hover:border-sky-300'}`}>
        {icon && <span className="text-slate-400 shrink-0">{icon}</span>}
        <span className={`flex-1 text-left truncate ${current ? '' : 'text-slate-400'}`}>{current?.label ?? placeholder}</span>
        <ChevronDown className={`w-4 h-4 text-slate-400 shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div ref={listRef} role="listbox" aria-label={label || placeholder}
            initial={{ opacity: 0, y: -6, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }} transition={{ duration: 0.16 }}
            className={`absolute z-30 mt-2 min-w-full max-h-64 overflow-auto rounded-2xl bg-white border border-slate-200 shadow-xl p-1.5 ${align === 'right' ? 'right-0' : 'left-0'}`}
            style={{ boxShadow: '0 16px 40px -12px rgba(37,99,235,0.25)' }}>
            {opts.map((o, i) => {
              const active = String(o.value) === String(value)
              return (
                <button key={o.value} type="button" role="option" aria-selected={active} data-active={active}
                  onClick={() => commit(o.value)} onMouseEnter={() => setHi(i)} tabIndex={-1}
                  className={`w-full flex items-center justify-between gap-2 rounded-xl px-3 py-2 text-sm text-left transition
                    ${active ? 'bg-grad-accent text-white font-semibold' : hi === i ? 'bg-sky-50 text-slate-700' : 'text-slate-600 hover:bg-sky-50'}`}>
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
