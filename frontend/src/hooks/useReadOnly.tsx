import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api'

/**
 * 演示站只读模式。
 *
 * 服务端 `app/guards.py` 是真正的闸门（前端藏没藏按钮，它都拦）。这里只负责别让用户
 * 点到一个注定 403 的按钮——两层各自独立生效，不互相依赖。
 *
 * 取不到 /api/health 时按「可写」处理：本地开发、离线跑脚本都不该被前端自己锁死。
 */
const ReadOnlyContext = createContext(false)

export function ReadOnlyProvider({ children }: { children: ReactNode }) {
  const [readOnly, setReadOnly] = useState(false)
  useEffect(() => {
    let alive = true
    api.health()
      .then(h => { if (alive) setReadOnly(Boolean(h.read_only)) })
      .catch(() => { /* 探测失败即按可写处理 */ })
    return () => { alive = false }
  }, [])
  return <ReadOnlyContext.Provider value={readOnly}>{children}</ReadOnlyContext.Provider>
}

export function useReadOnly() {
  return useContext(ReadOnlyContext)
}
