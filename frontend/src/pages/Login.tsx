import { FormEvent, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, BriefcaseBusiness, CheckCircle2, Eye, EyeOff, Loader2, LockKeyhole, UserRound } from 'lucide-react'
import { roleHome, useAuth } from '../auth'
import { errMsg } from '../api'
import { useToast } from '../components/Toast'

/* 左栏能力预览：用系统真实界面截图，避免登录页只剩一句空口号 */
const PREVIEWS = [
  { img: '/shot-dashboard-sm.webp', title: '数据驾驶舱', text: '语料、置信与闭环漏斗' },
  { img: '/shot-panorama-sm.webp', title: '全景能力图谱', text: '岗位与能力点关系网' },
  { img: '/shot-match-sm.webp', title: '人岗匹配诊断', text: '差距定位与学习路径' },
]

const GUARANTEES = ['版本可追溯', '个人数据隔离', '组织权限边界']

export default function Login({ register = false }: { register?: boolean }) {
  const auth = useAuth()
  const navigate = useNavigate()
  const location = useLocation() as any
  const toast = useToast()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<'user' | 'hr'>('user')
  const [organizationName, setOrganizationName] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (username.trim().length < 3 || password.length < 8) {
      toast('error', '用户名至少 3 位，密码至少 8 位')
      return
    }
    if (register && role === 'hr' && !organizationName.trim()) {
      toast('error', 'HR 账号需要填写组织名称')
      return
    }
    setLoading(true)
    try {
      const user = register
        ? await auth.register({ username: username.trim(), password, role, organization_name: organizationName.trim() || undefined })
        : await auth.login(username.trim(), password)
      toast('success', register ? '账号创建成功' : '登录成功')
      const fallback = roleHome(user.role)
      navigate(location.state?.from || fallback, { replace: true })
    } catch (error) {
      toast('error', errMsg(error, register ? '注册失败' : '登录失败'))
    } finally { setLoading(false) }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-surface-page">
      <img src="/login-background.webp" alt="" className="absolute inset-0 h-full w-full object-cover" onError={event => { event.currentTarget.hidden = true }} />
      {/* 遮罩方向跟着图走：/login-background.webp 的光晕在左半边、留白在右半边，
          所以左侧薄（0.45，让光晕透出来）、右侧厚（0.86，把表单底下压干净）。
          此前是 0.98/0.90/0.66 全程压死，改薄后又一度左浓右淡，与图正好错开。 */}
      <div className="absolute inset-0 bg-[linear-gradient(100deg,rgb(var(--surface-page)/0.45),rgb(var(--surface-page)/0.62)_44%,rgb(var(--surface-page)/0.86))]" />
      <div className="relative z-10 mx-auto grid min-h-screen max-w-[1480px] lg:grid-cols-[minmax(0,1fr)_minmax(420px,520px)]">
        {/* 左栏：顶部返回 → 中部品牌/主张/能力预览（撑满剩余高度）→ 底部保证项。
            原实现是 justify-between + 两个子块，中间会裂出一大片空白。 */}
        <section className="hidden min-w-0 flex-col px-12 py-10 lg:flex">
          <Link to="/" className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-body-2 transition hover:text-body-1">
            <ArrowLeft className="h-4 w-4" />返回首页
          </Link>

          <div className="flex min-h-0 flex-1 flex-col justify-center py-10">
            <div className="inline-flex items-center gap-2 text-sm font-semibold text-accent">
              <BriefcaseBusiness className="h-4 w-4" /> TalentGraph Workspace
            </div>
            <h1 className="mt-5 max-w-2xl text-4xl font-extrabold leading-tight text-body-1 xl:text-[2.75rem]">
              让岗位证据、人才能力和团队决策<br />在同一条链路上对齐
            </h1>
            <p className="mt-5 max-w-xl text-sm leading-7 text-body-2">
              登录后按角色进入对应工作区：个人保存匹配历史，HR 按组织管理候选人与团队盘点，管理员负责审核与发布。
            </p>

            <div className="mt-9 grid max-w-2xl grid-cols-3 gap-4">
              {PREVIEWS.map(item => (
                <div key={item.title} className="min-w-0 overflow-hidden rounded-2xl border border-line-soft/12 bg-white/85 shadow-[0_10px_30px_-20px_rgb(var(--brand-ink)/0.35)] backdrop-blur-sm">
                  <img src={item.img} alt="" className="block h-24 w-full object-cover object-top" loading="lazy"
                    onError={event => { event.currentTarget.hidden = true }} />
                  <div className="px-3 py-2.5">
                    <div className="truncate text-xs font-bold text-body-1">{item.title}</div>
                    <div className="mt-0.5 truncate text-[11px] text-body-3">{item.text}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="grid max-w-2xl grid-cols-3 gap-4 text-xs">
            {GUARANTEES.map(label => (
              <div key={label} className="flex items-center gap-2 border-t border-line-soft/18 pt-3 text-body-2">
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-accent" />{label}
              </div>
            ))}
          </div>
        </section>

        <section className="flex min-h-screen min-w-0 flex-col justify-center border-l border-line-soft/12 bg-white/[0.92] px-5 py-20 shadow-[-16px_0_36px_-32px_rgb(var(--brand-ink)/0.35)] backdrop-blur-md sm:px-10 lg:px-12">
          <Link to="/" className="mb-10 inline-flex w-fit items-center gap-2 text-sm font-semibold text-body-2 transition hover:text-body-1 lg:hidden">
            <ArrowLeft className="h-4 w-4" />返回首页
          </Link>
          <div className="mb-8">
            <div className="flex items-center gap-3">
              <img src="/logo.png" alt="" className="w-11 h-11 rounded-xl border border-line-soft/10" />
              <div><div className="font-extrabold text-xl text-body-1">智岗图谱</div><div className="text-xs text-body-3">TalentGraph AI</div></div>
            </div>
            <h2 className="text-2xl font-extrabold text-body-1 mt-8">{register ? '创建业务账号' : '登录工作台'}</h2>
            <p className="text-sm text-body-2 mt-1">{register ? '个人用户保存匹配历史，HR 按组织管理候选人' : '登录后进入与你的角色对应的安全工作区'} </p>
          </div>

          <form onSubmit={submit} className="space-y-4">
            {register && (
              <div>
                <div className="label mb-2">账号类型</div>
                <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="账号类型">
                  {([['user', '个人用户'], ['hr', 'HR 用户']] as const).map(([value, label]) => (
                    <button key={value} type="button" role="radio" aria-checked={role === value} onClick={() => setRole(value)}
                      className={role === value ? 'btn-primary justify-center' : 'btn-ghost justify-center'}>{label}</button>
                  ))}
                </div>
              </div>
            )}
            <label className="block">
              <span className="label block mb-2">用户名</span>
              <span className="relative block"><UserRound className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-body-3" />
                <input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} className="input pl-10" placeholder="输入用户名" /></span>
            </label>
            <label className="block">
              <span className="label block mb-2">密码</span>
              <span className="relative block"><LockKeyhole className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-body-3" />
                <input type={showPassword ? 'text' : 'password'} autoComplete={register ? 'new-password' : 'current-password'} value={password}
                  onChange={e => setPassword(e.target.value)} className="input pl-10 pr-11" placeholder="至少 8 位" />
                <button type="button" onClick={() => setShowPassword(v => !v)} aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  className="absolute right-2 top-1/2 -translate-y-1/2 w-8 h-8 grid place-items-center text-body-3 hover:text-body-1">
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button></span>
            </label>
            {register && role === 'hr' && (
              <label className="block"><span className="label block mb-2">组织名称</span>
                <input value={organizationName} onChange={e => setOrganizationName(e.target.value)} className="input" placeholder="例：某科技有限公司" /></label>
            )}
            <button type="submit" disabled={loading} className="btn-primary w-full justify-center !mt-6">
              {loading && <Loader2 className="w-4 h-4 animate-spin" />}{register ? '创建账号' : '登录'}
            </button>
          </form>

          <div className="text-sm text-body-2 mt-6 text-center">
            {register ? '已有账号？' : '还没有账号？'}{' '}
            <Link to={register ? '/login' : '/register'} className="text-accent font-semibold hover:underline">{register ? '去登录' : '立即注册'}</Link>
          </div>
        </section>
      </div>
    </div>
  )
}
