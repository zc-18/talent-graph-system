import { FormEvent, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { BriefcaseBusiness, Eye, EyeOff, Loader2, LockKeyhole, UserRound } from 'lucide-react'
import { useAuth } from '../auth'
import { errMsg } from '../api'
import { Card } from '../components/ui'
import { useToast } from '../components/Toast'

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
      const fallback = user.role === 'admin' ? '/admin' : user.role === 'hr' ? '/hr' : '/history'
      navigate(location.state?.from || fallback, { replace: true })
    } catch (error) {
      toast('error', errMsg(error, register ? '注册失败' : '登录失败'))
    } finally { setLoading(false) }
  }

  return (
    <div className="min-h-[calc(100vh-7rem)] grid place-items-center py-6">
      <div className="w-full max-w-5xl grid lg:grid-cols-[1.05fr_0.95fr] overflow-hidden rounded-2xl border border-slate-200/80 bg-white/75 shadow-card">
        <section className="hidden lg:flex relative min-h-[590px] flex-col justify-between overflow-hidden bg-slate-950 p-10 text-white">
          <div className="absolute inset-0 opacity-35 bg-[url('/graph-bg2.webp')] bg-cover bg-center" />
          <div className="absolute inset-0 bg-gradient-to-b from-slate-950/20 via-slate-950/55 to-slate-950" />
          <div className="relative">
            <div className="inline-flex items-center gap-2 text-sm text-cyan-200"><BriefcaseBusiness className="w-4 h-4" /> TalentGraph Workspace</div>
            <h1 className="mt-5 text-4xl font-extrabold leading-tight">让岗位证据、人才能力和团队决策在同一条链路上对齐</h1>
          </div>
          <div className="relative grid grid-cols-3 gap-3 text-xs">
            {['版本可追溯', '个人数据隔离', '组织权限边界'].map(label => (
              <div key={label} className="border-t border-white/20 pt-3 text-slate-300">{label}</div>
            ))}
          </div>
        </section>

        <Card className="!rounded-none !border-0 !shadow-none p-6 sm:p-10 lg:p-12 flex flex-col justify-center">
          <div className="mb-8">
            <div className="flex items-center gap-3">
              <img src="/logo.png" alt="" className="w-11 h-11 rounded-xl border border-slate-100" />
              <div><div className="font-extrabold text-xl text-slate-900">智岗图谱</div><div className="text-xs text-slate-400">TalentGraph AI</div></div>
            </div>
            <h2 className="text-2xl font-extrabold text-slate-900 mt-8">{register ? '创建业务账号' : '登录工作台'}</h2>
            <p className="text-sm text-slate-500 mt-1">{register ? '个人用户保存匹配历史，HR 按组织管理候选人' : '匿名仍可浏览公共图谱，登录后使用私有业务数据'} </p>
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
              <span className="relative block"><UserRound className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} className="input pl-10" placeholder="输入用户名" /></span>
            </label>
            <label className="block">
              <span className="label block mb-2">密码</span>
              <span className="relative block"><LockKeyhole className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input type={showPassword ? 'text' : 'password'} autoComplete={register ? 'new-password' : 'current-password'} value={password}
                  onChange={e => setPassword(e.target.value)} className="input pl-10 pr-11" placeholder="至少 8 位" />
                <button type="button" onClick={() => setShowPassword(v => !v)} aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  className="absolute right-2 top-1/2 -translate-y-1/2 w-8 h-8 grid place-items-center text-slate-400 hover:text-slate-700">
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

          <div className="text-sm text-slate-500 mt-6 text-center">
            {register ? '已有账号？' : '还没有账号？'}{' '}
            <Link to={register ? '/login' : '/register'} className="text-accent font-semibold hover:underline">{register ? '去登录' : '立即注册'}</Link>
          </div>
        </Card>
      </div>
    </div>
  )
}
