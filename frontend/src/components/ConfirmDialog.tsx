import { useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'

export default function ConfirmDialog({ open, title, description, confirmText = '确认删除', danger = true, onConfirm, onCancel }: {
  open: boolean
  title: string
  description?: string
  confirmText?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    confirmRef.current?.focus()
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [open, onCancel])

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div className="fixed inset-0 z-[90] bg-slate-900/40 backdrop-blur-sm"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onCancel} />
          <motion.div role="alertdialog" aria-modal="true" aria-label={title}
            className="fixed z-[95] left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[calc(100vw-2rem)] max-w-sm rounded-2xl bg-white border border-slate-200 p-5 shadow-2xl"
            initial={{ opacity: 0, scale: 0.94, y: 8 }} animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 6 }} transition={{ duration: 0.18 }}>
            <div className="flex items-start gap-3">
              <div className={`w-10 h-10 rounded-xl grid place-items-center shrink-0 ${danger ? 'bg-rose-50 text-rose-500' : 'bg-sky-50 text-sky-500'}`}>
                <AlertTriangle className="w-5 h-5" />
              </div>
              <div className="min-w-0">
                <h3 className="font-bold text-slate-900">{title}</h3>
                {description && <p className="text-sm text-slate-500 mt-1 leading-relaxed">{description}</p>}
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={onCancel} className="btn-ghost">取消</button>
              <button ref={confirmRef} onClick={onConfirm}
                className={`btn text-white ${danger ? 'bg-rose-500 hover:bg-rose-600' : 'bg-grad-accent hover:brightness-105'} active:scale-[0.98]`}>
                {confirmText}
              </button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
