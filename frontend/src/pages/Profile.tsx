import { useEffect, useRef, useState } from 'react'
import { Check, Loader2, Upload } from 'lucide-react'
import { api, errMsg, AuthUser } from '../api'
import { useAuth } from '../auth'
import { Card, PageHeader, ErrorState } from '../components/ui'
import { ITalent, IReview } from '../components/icons'
import { useToast } from '../components/Toast'
import RoleAvatar from '../components/RoleAvatar'

const NICKNAME_MIN = 1
const NICKNAME_MAX = 64
/** 后端 413 的阈值；这里先挡一道，避免把 2MB+ 的图整包传上去再被拒 */
const FALLBACK_MAX_BYTES = 2 * 1024 * 1024
const ACCEPT = 'image/png,image/jpeg,image/webp'

function nicknameError(value: string): string | null {
  const trimmed = value.trim()
  if (trimmed.length < NICKNAME_MIN) return '昵称不能为空'
  if (trimmed.length > NICKNAME_MAX) return `昵称最多 ${NICKNAME_MAX} 个字符`
  if (/[\r\n]/.test(value)) return '昵称不能包含换行'
  return null
}

export default function Profile() {
  const { user, applyUser } = useAuth()
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)

  const [presets, setPresets] = useState<string[]>([])
  const [maxBytes, setMaxBytes] = useState(FALLBACK_MAX_BYTES)
  const [presetError, setPresetError] = useState(false)
  const [nickname, setNickname] = useState(user?.nickname || user?.username || '')
  // 选中的头像：null 表示"沿用当前"，不随保存一起提交
  const [avatar, setAvatar] = useState<string | null>(user?.avatar_url || null)
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)

  const loadPresets = () => {
    setPresetError(false)
    api.avatarPresets()
      .then(data => { setPresets(data.items || []); if (data.max_upload_bytes) setMaxBytes(data.max_upload_bytes) })
      .catch(() => setPresetError(true))
  }
  useEffect(loadPresets, [])

  useEffect(() => {
    if (!user) return
    setNickname(user.nickname || user.username)
    setAvatar(user.avatar_url || null)
  }, [user?.id, user?.nickname, user?.avatar_url])

  if (!user) return null

  const nickErr = nicknameError(nickname)
  const dirty = nickname.trim() !== (user.nickname || user.username) || (avatar || null) !== (user.avatar_url || null)

  const accept = (next: AuthUser) => {
    applyUser(next)
    toast('success', '资料已更新')
  }

  const save = async () => {
    if (nickErr || saving) return
    setSaving(true)
    try {
      const body: { nickname?: string; avatar_url?: string } = { nickname: nickname.trim() }
      if (avatar) body.avatar_url = avatar
      accept(await api.updateProfile(body))
    } catch (error) {
      toast('error', errMsg(error, '保存失败'))
    } finally { setSaving(false) }
  }

  const upload = async (file: File) => {
    if (!ACCEPT.split(',').includes(file.type)) {
      toast('error', '仅支持 PNG / JPEG / WebP 图片')
      return
    }
    if (file.size > maxBytes) {
      toast('error', `图片不能超过 ${(maxBytes / 1024 / 1024).toFixed(0)}MB`)
      return
    }
    setUploading(true)
    try {
      const result = await api.uploadAvatar(file)
      setAvatar(result.avatar_url)
      // 上传接口本身已经落库并回了完整 actor，直接写回全局状态
      accept(result.user)
    } catch (error) {
      toast('error', errMsg(error, '头像上传失败'))
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const previewName = nickname.trim() || user.username
  const roleLabel = user.role === 'admin' ? '管理员' : user.role === 'hr' ? 'HR' : '个人用户'

  return (
    <div className="space-y-5">
      <PageHeader icon={<IReview className="w-6 h-6" />} title="账号资料"
        subtitle="昵称与头像会显示在侧栏、匹配记录和团队协作里" />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
        {/* 预览 + 昵称 */}
        <Card className="tg-topbar relative overflow-hidden p-5" delay={0}>
          <div className="flex items-center gap-3">
            <RoleAvatar username={user.username} role={user.role} avatarUrl={avatar}
              className="h-16 w-16 shrink-0 rounded-2xl border border-white shadow-sm" />
            <div className="min-w-0">
              <div className="truncate text-base font-bold text-body-1">{previewName}</div>
              <div className="truncate text-xs text-body-3">
                {user.username} · {roleLabel}{user.organization_name ? ` · ${user.organization_name}` : ''}
              </div>
            </div>
          </div>

          <label className="label mt-5 block" htmlFor="profile-nickname">昵称</label>
          <input id="profile-nickname" value={nickname} maxLength={NICKNAME_MAX + 10}
            onChange={event => setNickname(event.target.value)}
            className="input mt-1.5" placeholder="用于展示的名字" />
          <div className="mt-1.5 flex items-center justify-between gap-2 text-[11px]">
            <span className={nickErr ? 'text-danger' : 'text-body-3'}>{nickErr || '1–64 个字符'}</span>
            <span className="tabular-nums text-body-3">{nickname.trim().length}/{NICKNAME_MAX}</span>
          </div>

          <button onClick={() => void save()} disabled={!!nickErr || saving || !dirty}
            className="btn-primary mt-4 w-full justify-center">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            保存资料
          </button>
        </Card>

        {/* 头像选择 */}
        <Card className="p-5" delay={0.05}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2 font-semibold text-body-1">
                <ITalent className="h-4 w-4 text-accent-deep" />选择头像
              </div>
              <p className="mt-1 text-xs text-body-2">从预置图库中选一张，或上传自己的图片（≤ {(maxBytes / 1024 / 1024).toFixed(0)}MB）</p>
            </div>
            <button onClick={() => fileRef.current?.click()} disabled={uploading} className="btn-ghost">
              {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              上传自定义头像
            </button>
            <input ref={fileRef} type="file" accept={ACCEPT} className="hidden"
              onChange={event => { const f = event.target.files?.[0]; if (f) void upload(f) }} />
          </div>

          {presetError ? (
            <ErrorState text="头像图库加载失败" onRetry={loadPresets} />
          ) : (
            <div className="mt-4 grid grid-cols-4 gap-3 sm:grid-cols-6">
              {presets.map(url => {
                const active = avatar === url
                return (
                  <button key={url} type="button" onClick={() => setAvatar(url)}
                    aria-pressed={active} title="选择这张头像"
                    className={`relative aspect-square overflow-hidden rounded-2xl border transition ${
                      active
                        ? 'border-accent ring-2 ring-accent/35 shadow-glow'
                        : 'border-line-soft/8 hover:border-accent/40'}`}>
                    <img src={url} alt="" loading="lazy" className="h-full w-full object-cover" />
                    {active && (
                      <span className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded-full bg-grad-accent text-white shadow-sm">
                        <Check className="h-3 w-3" />
                      </span>
                    )}
                  </button>
                )
              })}
              {presets.length === 0 && (
                <div className="col-span-full py-8 text-center text-xs text-body-3">头像图库加载中…</div>
              )}
            </div>
          )}

          <p className="mt-4 text-[11px] leading-5 text-body-3">
            头像仅用于站内展示。上传的图片会保存在本站，选择预置头像不会上传任何文件。
          </p>
        </Card>
      </div>
    </div>
  )
}
