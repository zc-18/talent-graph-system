import { createContext, useCallback, useContext, useRef, useState, ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { CheckCircle2, XCircle, Info } from 'lucide-react'

type ToastKind = 'success' | 'error' | 'info'
type ToastItem = { id: number; kind: ToastKind; text: string }

const ToastCtx = createContext<(kind: ToastKind, text: string) => void>(() => {})

export function useToast() { return useContext(ToastCtx) }

const ICON = {
  success: <CheckCircle2 className="w-[18px] h-[18px] text-accent-deep shrink-0" />,
  error: <XCircle className="w-[18px] h-[18px] text-danger shrink-0" />,
  info: <Info className="w-[18px] h-[18px] text-accent shrink-0" />,
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const idRef = useRef(0)

  const push = useCallback((kind: ToastKind, text: string) => {
    const id = ++idRef.current
    setItems(list => [...list.slice(-3), { id, kind, text }])
    setTimeout(() => setItems(list => list.filter(t => t.id !== id)), kind === 'error' ? 5000 : 3000)
  }, [])

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed top-16 lg:top-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2 pointer-events-none px-4 w-full sm:w-auto"
        role="status" aria-live="polite">
        <AnimatePresence>
          {items.map(t => (
            <motion.div key={t.id}
              initial={{ opacity: 0, y: -12, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.96 }} transition={{ duration: 0.2 }}
              className="pointer-events-auto flex items-center gap-2.5 max-w-full sm:max-w-md rounded-xl bg-white/95 backdrop-blur-xl border border-line-soft/8 px-4 py-2.5 text-sm text-body-1 shadow-xl"
              style={{ boxShadow: '0 12px 32px -8px rgba(15,23,42,0.18)' }}>
              {ICON[t.kind]}
              <span className="leading-snug">{t.text}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  )
}
