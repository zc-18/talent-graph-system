import { useEffect, useState } from 'react'
import type { AppRole } from '../api'

/* 头像来源优先级：
   1) 后端 users.avatar_url（Lane C 正在补这个字段）——用可选链兜住，字段没上线时不崩；
   2) 按 username 哈希落到 /avatars/a01.webp … a12.webp 里的一张（同一个人恒定同一张）；
   3) onError 再回退到角色兜底图。
   旧实现写死的 /w1.webp、/chaun-1.jpg、/chuan.jpg 在 public/ 里根本不存在，三个角色头像全 404。 */

const AVATAR_POOL = Array.from({ length: 12 }, (_, i) => `/avatars/a${String(i + 1).padStart(2, '0')}.webp`)

const ROLE_FALLBACK: Record<AppRole, string> = {
  user: '/avatars/a01.webp',
  hr: '/avatars/a05.webp',
  admin: '/avatars/a09.webp',
}

/** FNV-1a，稳定且与运行环境无关：同一 username 在任何设备上都取到同一张图 */
function pickByUsername(username: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < username.length; i++) {
    h ^= username.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return AVATAR_POOL[h % AVATAR_POOL.length]
}

export default function RoleAvatar({ username, role, avatarUrl, className = '' }: {
  username: string; role: AppRole; avatarUrl?: string | null; className?: string
}) {
  const preferred = avatarUrl?.trim() || pickByUsername(username)
  const fallback = ROLE_FALLBACK[role] || AVATAR_POOL[0]
  const [src, setSrc] = useState(preferred)
  useEffect(() => setSrc(preferred), [preferred])
  return (
    <img
      src={src}
      alt={`${username} 头像`}
      onError={() => { if (src !== fallback) setSrc(fallback) }}
      className={`object-cover bg-surface-muted ${className}`}
    />
  )
}
