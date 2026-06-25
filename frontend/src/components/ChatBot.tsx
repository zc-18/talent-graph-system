import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X, Send, Sparkles } from 'lucide-react'

interface Msg { role: 'user' | 'assistant'; content: string }

const WELCOME = '你好，我是「智岗小助手」👋\n我可以帮你了解平台功能、解读岗位能力要求，或给出职业规划与学习路径建议。试试下面的问题，或直接输入～'

function renderText(t: string) {
  // 简单 **加粗** 渲染
  return t.split(/(\*\*.+?\*\*)/g).map((p, i) =>
    p.startsWith('**') && p.endsWith('**')
      ? <strong key={i} className="font-semibold text-slate-900">{p.slice(2, -2)}</strong>
      : <span key={i}>{p}</span>)
}

export default function ChatBot() {
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState<Msg[]>([{ role: 'assistant', content: WELCOME }])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch('/api/chat/suggestions').then(r => r.json()).then(d => setSuggestions(d.items || [])).catch(() => {})
  }, [])
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [msgs, loading])

  const send = async (text: string) => {
    const q = text.trim()
    if (!q || loading) return
    setInput('')
    const history = msgs.filter(m => m.content !== WELCOME)
    setMsgs(m => [...m, { role: 'user', content: q }, { role: 'assistant', content: '' }])
    setLoading(true)
    try {
      const resp = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: q, history }),
      })
      if (!resp.body) throw new Error('no stream')
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop() || ''
        for (const part of parts) {
          const line = part.replace(/^data:\s?/, '')
          if (!line || line === '[DONE]') continue
          try {
            const obj = JSON.parse(line)
            if (obj.delta) setMsgs(m => {
              const copy = [...m]; copy[copy.length - 1] = { role: 'assistant', content: copy[copy.length - 1].content + obj.delta }; return copy
            })
          } catch { /* ignore */ }
        }
      }
    } catch {
      setMsgs(m => { const c = [...m]; c[c.length - 1] = { role: 'assistant', content: '抱歉，助手暂时不可用，请稍后再试。' }; return c })
    } finally { setLoading(false) }
  }

  return (
    <>
      {/* 悬浮按钮 */}
      <motion.button
        onClick={() => setOpen(o => !o)}
        initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
        transition={{ delay: 0.6, type: 'spring', stiffness: 260, damping: 18 }}
        whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.95 }}
        className="fixed bottom-6 right-6 z-50 rounded-full shadow-glow grid place-items-center"
        style={{ width: 60, height: 60, background: 'linear-gradient(135deg,#6366F1,#22D3EE)' }}
        aria-label="AI助手">
        {open
          ? <X className="w-6 h-6 text-white" />
          : <img src="/avatar.webp" alt="助手" className="w-12 h-12 rounded-full object-cover ring-2 ring-white/80" />}
      </motion.button>

      {/* 对话面板 */}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 24, scale: 0.92 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.92 }} transition={{ type: 'spring', stiffness: 300, damping: 26 }}
            style={{ transformOrigin: 'bottom right' }}
            className="fixed bottom-24 left-4 right-4 sm:left-auto sm:right-6 z-50 w-auto sm:w-[384px] h-[560px] max-h-[calc(100vh-8rem)] flex flex-col rounded-3xl overflow-hidden bg-white shadow-2xl border border-white/80">
            {/* 头部 */}
            <div className="px-4 py-3.5 flex items-center gap-3 text-white" style={{ background: 'linear-gradient(135deg,#6366F1,#22D3EE)' }}>
              <img src="/avatar.webp" alt="" className="w-11 h-11 rounded-full object-cover ring-2 ring-white/70 bg-white" />
              <div className="flex-1 min-w-0">
                <div className="font-bold text-[15px] flex items-center gap-1.5">智岗小助手 <Sparkles className="w-3.5 h-3.5" /></div>
                <div className="text-[11px] text-white/85 flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-300 inline-block" /> 在线 · 随时为你解答
                </div>
              </div>
              <button onClick={() => setOpen(false)} className="text-white/80 hover:text-white p-1"><X className="w-5 h-5" /></button>
            </div>

            {/* 消息区 */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto px-3.5 py-4 space-y-3 bg-gradient-to-b from-sky-50/60 to-white">
              {msgs.map((m, i) => (
                <div key={i} className={`flex gap-2 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
                  {m.role === 'assistant'
                    ? <img src="/avatar.webp" className="w-7 h-7 rounded-full object-cover shrink-0 mt-0.5 bg-white border border-slate-100" />
                    : <img src="/user-avatar.jpeg" className="w-7 h-7 rounded-full object-cover shrink-0 mt-0.5 border border-slate-100" />}
                  <div className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed whitespace-pre-wrap break-words ${
                    m.role === 'user'
                      ? 'bg-grad-accent text-white rounded-tr-sm'
                      : 'bg-white text-slate-700 border border-slate-200 shadow-sm rounded-tl-sm'}`}>
                    {m.content ? renderText(m.content)
                      : <span className="inline-flex gap-1 py-1">
                          {[0, 1, 2].map(d => <span key={d} className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: `${d * 0.15}s` }} />)}
                        </span>}
                  </div>
                </div>
              ))}
              {msgs.length <= 1 && suggestions.length > 0 && (
                <div className="flex flex-wrap gap-2 pt-1 pl-9">
                  {suggestions.map(s => (
                    <button key={s} onClick={() => send(s)}
                      className="text-[12px] px-3 py-1.5 rounded-full bg-white border border-indigo-200 text-indigo-600 hover:bg-indigo-50 transition">
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* 输入区 */}
            <div className="p-3 border-t border-slate-100 bg-white">
              <div className="flex items-end gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 focus-within:border-accent/60 focus-within:ring-2 focus-within:ring-accent/15 transition">
                <textarea
                  value={input} onChange={e => setInput(e.target.value)} rows={1}
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) } }}
                  placeholder="输入你的问题…" disabled={loading}
                  className="flex-1 resize-none bg-transparent outline-none text-[13px] text-slate-700 placeholder:text-slate-400 max-h-24" />
                <button onClick={() => send(input)} disabled={loading || !input.trim()}
                  className="shrink-0 w-8 h-8 rounded-xl grid place-items-center text-white disabled:opacity-40 transition"
                  style={{ background: 'linear-gradient(135deg,#6366F1,#22D3EE)' }}>
                  <Send className="w-4 h-4" />
                </button>
              </div>
              <div className="text-[10px] text-slate-300 text-center mt-1.5">智岗小助手 · 由 DeepSeek 大模型驱动</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
