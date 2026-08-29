import { ReactNode, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X, Send, Sparkles } from 'lucide-react'

interface Msg { role: 'user' | 'assistant'; content: string }

const WELCOME = '你好，我是「智岗小助手」👋\n我可以帮你了解平台功能、解读岗位能力要求，或给出职业规划与学习路径建议。试试下面的问题，或直接输入～'

/* ────────────────────────────────────────────────────────────────
   轻量 Markdown 渲染
   后端 prompt（backend/app/routers/chat.py）要求助手「适当用要点」，
   此前这里只处理 **加粗**，于是 `- 列表`、`## 标题`、`1. 有序项`、`行内代码`
   全是裸文本，靠外层 whitespace-pre-wrap 硬撑。
   支持：标题 / 无序列表 / 有序列表 / 段落 / **加粗** / `行内代码`。
   全部走 React 元素，**不用 dangerouslySetInnerHTML**——内容来自大模型。
   ──────────────────────────────────────────────────────────────── */

/** 行内：**加粗** 与 `代码`。一次正则切分，避免嵌套解析带来的复杂度。 */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return parts.filter(Boolean).map((part, i) => {
    const key = `${keyPrefix}-${i}`
    if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) {
      return <strong key={key} className="font-semibold">{part.slice(2, -2)}</strong>
    }
    if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={key} className="rounded bg-brand-ink/8 px-1 py-0.5 font-mono text-[0.92em] text-current">
          {part.slice(1, -1)}
        </code>
      )
    }
    return <span key={key}>{part}</span>
  })
}

type Block =
  | { kind: 'h'; level: number; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; items: string[] }
  | { kind: 'p'; lines: string[] }

/** 按行扫描成块。同类型的连续行会合并，段落内的换行保留为 <br>。 */
function toBlocks(src: string): Block[] {
  const blocks: Block[] = []
  for (const rawLine of src.split('\n')) {
    const lineText = rawLine.trimEnd()
    const trimmed = lineText.trim()
    const last = blocks[blocks.length - 1]

    if (!trimmed) { blocks.push({ kind: 'p', lines: [] }); continue }

    const heading = /^(#{1,4})\s+(.*)$/.exec(trimmed)
    if (heading) { blocks.push({ kind: 'h', level: heading[1].length, text: heading[2] }); continue }

    const bullet = /^[-*+]\s+(.*)$/.exec(trimmed)
    if (bullet) {
      if (last?.kind === 'ul') last.items.push(bullet[1])
      else blocks.push({ kind: 'ul', items: [bullet[1]] })
      continue
    }

    const ordered = /^\d{1,3}[.)]\s+(.*)$/.exec(trimmed)
    if (ordered) {
      if (last?.kind === 'ol') last.items.push(ordered[1])
      else blocks.push({ kind: 'ol', items: [ordered[1]] })
      continue
    }

    if (last?.kind === 'p') last.lines.push(trimmed)
    else blocks.push({ kind: 'p', lines: [trimmed] })
  }
  return blocks.filter(b => b.kind !== 'p' || b.lines.length > 0)
}

function renderMarkdown(src: string): ReactNode {
  const blocks = toBlocks(src)
  return blocks.map((block, bi) => {
    const key = `b${bi}`
    if (block.kind === 'h') {
      // 面板很窄，四级标题在视觉上只分两档，不做五种字号
      const cls = block.level <= 2 ? 'text-[14px]' : 'text-[13px]'
      return (
        <div key={key} className={`${cls} font-bold text-body-1 first:mt-0 mt-2.5 mb-1`}>
          {renderInline(block.text, key)}
        </div>
      )
    }
    if (block.kind === 'ul') {
      return (
        <ul key={key} className="first:mt-0 mt-1.5 space-y-1 pl-1">
          {block.items.map((item, i) => (
            <li key={`${key}-${i}`} className="flex gap-2">
              <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-current opacity-50" />
              <span className="min-w-0 flex-1">{renderInline(item, `${key}-${i}`)}</span>
            </li>
          ))}
        </ul>
      )
    }
    if (block.kind === 'ol') {
      return (
        <ol key={key} className="first:mt-0 mt-1.5 space-y-1 pl-1">
          {block.items.map((item, i) => (
            <li key={`${key}-${i}`} className="flex gap-2">
              <span aria-hidden className="shrink-0 font-semibold tabular-nums opacity-70">{i + 1}.</span>
              <span className="min-w-0 flex-1">{renderInline(item, `${key}-${i}`)}</span>
            </li>
          ))}
        </ol>
      )
    }
    return (
      <p key={key} className="first:mt-0 mt-1.5">
        {block.lines.map((line, i) => (
          <span key={`${key}-${i}`}>
            {i > 0 && <br />}
            {renderInline(line, `${key}-${i}`)}
          </span>
        ))}
      </p>
    )
  })
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
      {/* 悬浮按钮：移动端更小更贴边，减少对内容的遮挡；主内容区已预留底部安全间距。
          注意 ChatBot 从来没有遮罩层（对比 App.tsx 的抽屉），这里也不新增。 */}
      <motion.button
        onClick={() => setOpen(o => !o)}
        initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
        transition={{ delay: 0.6, type: 'spring', stiffness: 260, damping: 18 }}
        whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.95 }}
        className="fixed bottom-4 right-4 lg:bottom-6 lg:right-6 z-50 grid w-12 h-12 place-items-center rounded-full
                   bg-grad-accent ring-1 ring-white/60 shadow-[0_12px_30px_rgb(var(--brand-accent)/0.38)]
                   lg:w-[60px] lg:h-[60px]"
        aria-label={open ? '关闭 AI 助手' : '打开 AI 助手'}>
        {open
          ? <X className="w-6 h-6 text-white" />
          : <img src="/assistant.webp" alt="" className="w-9 h-9 lg:w-12 lg:h-12 rounded-full object-cover ring-2 ring-white/80" />}
      </motion.button>

      {/* 对话面板：<sm 近全屏，≥sm 右下浮窗 */}
      <AnimatePresence>
        {open && (
          <motion.div role="dialog" aria-label="智岗小助手对话"
            initial={{ opacity: 0, y: 24, scale: 0.92 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.92 }} transition={{ type: 'spring', stiffness: 300, damping: 26 }}
            style={{ transformOrigin: 'bottom right' }}
            className="fixed inset-x-2 top-16 bottom-20 z-50 flex flex-col overflow-hidden rounded-2xl border border-line-soft/6 bg-white
                       shadow-[0_24px_60px_-20px_rgb(var(--brand-ink)/0.32)]
                       sm:inset-auto sm:bottom-24 sm:right-6 sm:h-[560px] sm:max-h-[calc(100vh-8rem)] sm:w-[384px]">
            {/* 头部：毛玻璃 + 3px 顶边饰条（原来是一整块 bg-grad-sky 实色面板） */}
            <div className="tg-topbar relative flex items-center gap-3 overflow-hidden border-b border-line-soft/6
                            bg-white/88 px-4 pb-3.5 pt-4 text-body-1 backdrop-blur-xl">
              <div aria-hidden className="absolute inset-0 pointer-events-none bg-cover bg-center opacity-[0.10]"
                style={{ backgroundImage: 'url(/chat-banner.webp)' }} />
              <img src="/assistant.webp" alt="" className="relative z-10 w-11 h-11 rounded-full object-cover bg-surface-muted ring-2 ring-white shadow-sm" />
              <div className="relative z-10 flex-1 min-w-0">
                <div className="font-bold text-[15px] flex items-center gap-1.5">智岗小助手 <Sparkles className="w-3.5 h-3.5 text-accent" /></div>
                <div className="text-[11px] text-body-2 flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-success inline-block" /> 在线 · 随时为你解答
                </div>
              </div>
              <button onClick={() => setOpen(false)} aria-label="关闭对话" className="relative z-10 text-body-3 hover:text-body-1 p-1"><X className="w-5 h-5" /></button>
            </div>

            {/* 消息区 */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto px-3.5 py-4 space-y-3 bg-gradient-to-b from-surface-muted to-white">
              {msgs.map((m, i) => (
                <div key={i} className={`flex gap-2 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
                  {m.role === 'assistant'
                    ? <img src="/assistant.webp" alt="" className="w-7 h-7 rounded-full object-cover shrink-0 mt-0.5 bg-surface-muted border border-line-soft/8" />
                    : <span aria-hidden className="w-7 h-7 rounded-full shrink-0 mt-0.5 grid place-items-center bg-grad-accent text-[11px] font-bold text-white">我</span>}
                  {/* markdown 渲染出的是块级元素，外层不能再挂 whitespace-pre-wrap，
                      否则列表/标题的块级布局会和保留的空白打架 */}
                  <div className={`max-w-[80%] break-words rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed ${
                    m.role === 'user'
                      ? 'bg-grad-accent text-white shadow-[0_8px_18px_-10px_rgb(var(--brand-accent)/0.6)] rounded-tr-sm'
                      : 'bg-white text-body-1 border border-line-soft/6 shadow-sm rounded-tl-sm'}`}>
                    {m.content ? renderMarkdown(m.content)
                      : <span className="inline-flex gap-1 py-1">
                          {[0, 1, 2].map(d => <span key={d} className="w-1.5 h-1.5 rounded-full bg-body-3 animate-bounce" style={{ animationDelay: `${d * 0.15}s` }} />)}
                        </span>}
                  </div>
                </div>
              ))}
              {msgs.length <= 1 && suggestions.length > 0 && (
                <div className="flex flex-wrap gap-2 pt-1 pl-9">
                  {suggestions.map(s => (
                    <button key={s} onClick={() => send(s)}
                      className="rounded-full border border-accent/25 bg-white px-3 py-1.5 text-[12px] font-medium text-accent-deep
                                 transition hover:border-accent/50 hover:bg-accent/6">
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* 输入区 */}
            <div className="p-3 border-t border-line-soft/6 bg-white">
              <div className="flex items-end gap-2 rounded-2xl border border-line-soft/8 bg-surface-muted px-3 py-2 focus-within:border-accent/60 focus-within:ring-2 focus-within:ring-accent/15 transition">
                <textarea
                  value={input} onChange={e => setInput(e.target.value)} rows={1}
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) } }}
                  placeholder="输入你的问题…" disabled={loading}
                  className="flex-1 resize-none bg-transparent outline-none text-[13px] text-body-1 placeholder:text-body-3 max-h-24" />
                <button onClick={() => send(input)} disabled={loading || !input.trim()} aria-label="发送"
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-grad-accent text-white transition
                             shadow-[0_8px_18px_-10px_rgb(var(--brand-accent)/0.7)] disabled:opacity-40">
                  <Send className="w-4 h-4" />
                </button>
              </div>
              <div className="text-[10px] text-body-3 text-center mt-1.5">智岗小助手 · 由 DeepSeek 大模型驱动</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
