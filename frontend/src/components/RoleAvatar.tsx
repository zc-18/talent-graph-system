import { useEffect, useState } from 'react'
import type { AppRole } from '../api'

const USERNAME_AVATAR: Record<string, string> = {
  'demo-user': '/w1.webp',
  'demo-hr': '/chaun-1.jpg',
  'demo-admin': '/chuan.jpg',
}

const ROLE_FALLBACK: Record<AppRole, string> = {
  user: '/user-avatar.jpeg',
  hr: '/avatar.webp',
  admin: '/avatar.webp',
}

export default function RoleAvatar({ username, role, className = '' }: {
  username: string; role: AppRole; className?: string
}) {
  const preferred = USERNAME_AVATAR[username] || ROLE_FALLBACK[role]
  const fallback = ROLE_FALLBACK[role]
  const [src, setSrc] = useState(preferred)
  useEffect(() => setSrc(preferred), [preferred])
  return (
    <img
      src={src}
      alt={`${username} 头像`}
      onError={() => { if (src !== fallback) setSrc(fallback) }}
      className={`object-cover ${className}`}
    />
  )
}
