import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Link } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import {
  Upload, FileText, Loader2, CheckCircle2, XCircle, Sparkles, History, Plus, X,
} from 'lucide-react'
import { ITarget, IPath, ILightbulb, IUpload, IUser, IShieldCheck } from '../components/icons'
import { api, errMsg, JobListItem } from '../api'
import { Card, Badge, Spinner, ErrorState } from '../components/ui'
import Select from '../components/Select'
import { useToast } from '../components/Toast'
import { useFloat, useGrowLine, useReveal } from '../hooks/gsapFx'
import { useAuth } from '../auth'

export default function Match() {
  const loc = useLocation() as any
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [jobId, setJobId] = useState<number | null>(loc.state?.jobId ?? null)
  const [mode, setMode] = useState<'upload' | 'text'>('upload')
  const [targetMode, setTargetMode] = useState<'library' | 'text'>('library')
  const [targetJobText, setTargetJobText] = useState('')
  const [resumeText, setResumeText] = useState('')
  const [extracted, setExtracted] = useState<any>(null)
  const [confirmedSkills, setConfirmedSkills] = useState<string[]>([])
  const [newSkill, setNewSkill] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [jobsError, setJobsError] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const toast = useToast()
  const auth = useAuth()
  const floatRef = useFloat<HTMLImageElement>({ y: 10, duration: 3.2, deps: [result, loading] })
  const pathRef = useGrowLine('.path-line')
  const stepsRef = useReveal('[data-reveal]', { scroll: true, deps: [result] })

  const loadJobs = () => {
    setJobsError(false)
    api.jobs({ size: 100 }).then(d => { setJobs(d.items); if (!jobId && d.items[0]) setJobId(d.items[0].id) })
      .catch(() => setJobsError(true))
  }
  useEffect(() => { loadJobs() }, [])

  const onFile = async (f: File) => {
    const suffix = f.name.toLowerCase().slice(f.name.lastIndexOf('.'))
    if (suffix === '.doc') { toast('error', '旧版 .doc 二进制文件不支持，请转为 DOCX 后重试'); return }
    if (!['.pdf', '.docx', '.txt'].includes(suffix)) { toast('error', '仅支持 PDF、DOCX 和 TXT 简历'); return }
    if (f.size > 8 * 1024 * 1024) { toast('error', '文件超过 8 MB 限制'); return }
    setLoading(true); setExtracted(null); setResult(null)
    try {
      const r = await api.uploadResume(f)
      setExtracted(r.extracted)
      setConfirmedSkills(r.extracted?.skills || [])
      toast('success', `简历解析成功，提取 ${r.extracted?.skills?.length ?? 0} 项技能`)
    } catch (e: any) { toast('error', errMsg(e, '简历解析失败，请确认文件为 PDF/Word 格式')) }
    finally { setLoading(false) }
  }

  // 无任何简历输入（未上传解析成功、也没有粘贴文本）时禁止诊断
  const hasInput = mode === 'text' ? !!resumeText.trim() : confirmedSkills.length > 0
  const hasTarget = targetMode === 'library' ? !!jobId : targetJobText.trim().length >= 5

  const analyze = async () => {
    if (!hasTarget || !hasInput) return
    setLoading(true); setResult(null)
    try {
      const body: any = { generate_suggestions: true, save: auth.isAuthenticated }
      if (targetMode === 'library') body.job_id = jobId
      else body.target_job_text = targetJobText.trim()
      if (mode === 'text' && resumeText) body.resume_text = resumeText
      else if (extracted) { body.skills = confirmedSkills; body.skill_levels = extracted.skill_levels }
      else if (resumeText) body.resume_text = resumeText
      const r = await api.analyze(body)
      setResult(r)
    } catch (e) { toast('error', errMsg(e, '匹配诊断失败，请稍后重试')) }
    finally { setLoading(false) }
  }

  const res = result?.result
  const topGaps = res ? (res.top_gaps || res.missing_required || []).slice(0, 10) : []
  const radar = res && {
    radar: {
      indicator: Object.keys(res.dimension_scores).map(k => ({ name: k, max: 100 })),
      radius: '65%', axisName: { color: '#475569', fontSize: 11 },
      splitLine: { lineStyle: { color: 'rgba(2,6,23,0.08)' } },
      splitArea: { areaStyle: { color: ['rgba(99,102,241,0.05)', 'rgba(34,211,238,0.05)'] } },
      axisLine: { lineStyle: { color: 'rgba(2,6,23,0.08)' } },
    },
    series: [{
      type: 'radar', data: [{
        value: Object.values(res.dimension_scores),
        areaStyle: { color: 'rgba(99,102,241,0.22)' },
        lineStyle: { color: '#6366F1', width: 2 }, itemStyle: { color: '#0EA5E9' },
      }],
    }],
  }
  const gauge = res && {
    series: [{
      type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: 100, radius: '95%',
      progress: { show: true, width: 12, itemStyle: { color: res.overall_score >= 70 ? '#0284C7' : res.overall_score >= 50 ? '#38BDF8' : '#F59E0B' } },
      axisLine: { lineStyle: { width: 12, color: [[1, 'rgba(2,6,23,0.06)']] } },
      axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, pointer: { show: false },
      detail: { fontSize: 34, fontWeight: 800, color: '#0F172A', formatter: '{value}', offsetCenter: [0, '-5%'] },
      title: { show: false },
      data: [{ value: res.overall_score }],
    }],
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="w-11 h-11 shrink-0 rounded-xl bg-grad-accent ring-1 ring-accent/20 grid place-items-center shadow-glow">
          <ITarget className="w-6 h-6 text-accent-deep" />
        </div>
        <div>
          <h1 className="text-2xl font-extrabold text-slate-900">人岗匹配诊断与差距分析</h1>
          <p className="text-sm text-slate-500">简历解析（PDF/Word）· 多维度匹配 · 差距诊断 · 学习路径规划</p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 lg:gap-5">
        <Card className="p-5 space-y-4">
          <div>
            <div className="label mb-1.5">目标岗位</div>
            <div className="grid grid-cols-2 gap-1.5 mb-2"><button onClick={() => setTargetMode('library')} className={targetMode === 'library' ? 'btn-primary !py-2' : 'btn-ghost !py-2'}>岗位库</button><button onClick={() => setTargetMode('text')} className={targetMode === 'text' ? 'btn-primary !py-2' : 'btn-ghost !py-2'}>库外文本</button></div>
            {targetMode === 'library' ? (jobsError ? <ErrorState text="岗位列表加载失败" onRetry={loadJobs} /> : (
              <Select value={jobId ?? ''} onChange={v => setJobId(Number(v))} label="选择目标岗位"
                options={jobs.map(j => ({ value: String(j.id), label: j.name }))} />
            )) : <textarea value={targetJobText} onChange={e => setTargetJobText(e.target.value)} rows={4} className="input resize-none" placeholder="输入库外目标岗位的职责和能力要求，本次将生成私有临时画像" />}
          </div>

          <div className="flex gap-1.5">
            <button onClick={() => setMode('upload')} className={mode === 'upload' ? 'btn-primary flex-1' : 'btn-ghost flex-1'}>
              <Upload className="w-4 h-4" /> 上传简历
            </button>
            <button onClick={() => setMode('text')} className={mode === 'text' ? 'btn-primary flex-1' : 'btn-ghost flex-1'}>
              <FileText className="w-4 h-4" /> 粘贴文本
            </button>
          </div>

          {mode === 'upload' ? (
            <button type="button" onClick={() => fileRef.current?.click()}
              aria-label="上传 PDF 或 Word 简历文件"
              className="w-full border-2 border-dashed border-sky-200 rounded-2xl p-8 text-center cursor-pointer hover:border-accent/50 transition bg-sky-50/40 outline-none focus-visible:ring-2 focus-visible:ring-accent/40 focus-visible:border-accent/50">
              <input ref={fileRef} type="file" accept=".pdf,.docx,.txt" className="hidden" tabIndex={-1}
                onChange={e => e.target.files?.[0] && onFile(e.target.files[0])} />
              <IUpload className="w-8 h-8 mx-auto text-slate-400" />
              <p className="text-sm text-slate-600 mt-2">点击上传 PDF / DOCX / TXT 简历</p>
              <p className="text-[11px] text-slate-400 mt-1">提取准确率 ≥ 90%</p>
            </button>
          ) : (
            <textarea value={resumeText} onChange={e => setResumeText(e.target.value)} rows={7}
              className="input resize-none text-xs" placeholder="粘贴简历内容或技能列表…" />
          )}

          {extracted && (
            <div className="rounded-xl bg-sky-50/70 p-3">
              <div className="flex items-center gap-2 text-sm text-slate-700 mb-2">
                <IUser className="w-4 h-4 text-accent" /> {extracted.candidate_name || '候选人'} · {extracted.years_experience}年经验
              </div>
              <div className="text-[11px] text-slate-500 mb-2">请确认提取技能，点击 × 可移除误识别项</div>
              <div className="flex flex-wrap gap-1.5">{confirmedSkills.map(s => <span key={s} className="chip border bg-white border-slate-200 text-slate-700">{s}<button onClick={() => setConfirmedSkills(list => list.filter(item => item !== s))} aria-label={`移除 ${s}`}><X className="w-3 h-3" /></button></span>)}</div>
              <div className="flex gap-2 mt-2"><input value={newSkill} onChange={e => setNewSkill(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); if (newSkill.trim() && !confirmedSkills.includes(newSkill.trim())) setConfirmedSkills(list => [...list, newSkill.trim()]); setNewSkill('') } }} className="input !py-1.5" placeholder="补充技能" /><button onClick={() => { if (newSkill.trim() && !confirmedSkills.includes(newSkill.trim())) setConfirmedSkills(list => [...list, newSkill.trim()]); setNewSkill('') }} className="btn-ghost !p-2" aria-label="添加技能"><Plus className="w-4 h-4" /></button></div>
            </div>
          )}

          <button onClick={analyze} disabled={loading || !hasTarget || !hasInput} className="btn-primary w-full"
            title={hasInput ? undefined : '请先上传简历或粘贴文本'}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ITarget className="w-4 h-4" />} 开始匹配诊断
          </button>

          <div className="flex items-start gap-1.5 text-[11px] text-slate-400 leading-relaxed">
            <IShieldCheck className="w-3.5 h-3.5 shrink-0 mt-0.5 text-accent" />
            <span>隐私合规：简历仅在内存中解析用于本次匹配，原始简历与姓名等个人信息<b className="text-slate-500">不在服务器留存</b>，仅保留脱敏技能要素。</span>
          </div>
        </Card>

        <div ref={stepsRef} className="lg:col-span-2 space-y-5">
          {loading && !res && <Card className="p-10"><Spinner label="正在诊断人岗匹配度…" /></Card>}
          {!res && !loading && (
            <Card className="p-5 sm:p-8" delay={0.05}>
              <div className="text-center mb-6">
                <img ref={floatRef} src="/empty-match.webp" alt=""
                  className="mx-auto mb-2 h-32 w-32 object-contain mix-blend-multiply drop-shadow-[0_12px_24px_rgba(99,102,241,0.22)] sm:h-44 sm:w-44" />
                <h3 className="text-lg font-bold text-slate-800">上传简历或输入技能，开始诊断</h3>
                <p className="text-sm text-slate-500 mt-1">系统将对比目标岗位能力图谱，输出多维匹配与提升路径</p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {[
                  [ITarget, '综合匹配度', '加权必备/加分/级别/领域四维评分', '#6366F1'],
                  [IPath, '能力差距分析', '清晰列出已具备与缺失的必备技能', '#0EA5E9'],
                  [IPath, '学习路径规划', '按技能先修关系拓扑排序，分步提升', '#A855F7'],
                  [ILightbulb, '针对性建议', '大模型给出资源方向与达标周期', '#F59E0B'],
                ].map(([Icon, t, d, c]: any, i: number) => (
                  <div key={i} className="rounded-2xl bg-sky-50/70 border border-slate-200/60 p-4 flex gap-3">
                    <div className="w-9 h-9 rounded-xl grid place-items-center shrink-0" style={{ background: c }}>
                      <Icon className="w-5 h-5 text-white" />
                    </div>
                    <div>
                      <div className="text-[13px] font-bold text-slate-800">{t}</div>
                      <div className="text-[11px] text-slate-500 mt-0.5 leading-relaxed">{d}</div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-5 text-xs text-slate-400 text-center">简历技能提取准确率 ≥ 90% · 支持 PDF / Word / 文本</div>
            </Card>
          )}
          {res && (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <Card className="p-5">
                  <div className="label">综合匹配度</div>
                  <ReactECharts option={gauge} style={{ height: 180 }} />
                  <div className="text-center -mt-3">
                    <Badge tone={res.overall_score >= 70 ? 'indigo' : res.overall_score >= 50 ? 'cyan' : 'amber'}>{res.level}</Badge>
                  </div>
                </Card>
                <Card className="p-5">
                  <div className="label">多维度匹配雷达</div>
                  <ReactECharts option={radar} style={{ height: 210 }} />
                </Card>
              </div>

              <Card className="p-5">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                  <div>
                    <div className="flex items-center gap-2 label mb-2"><CheckCircle2 className="w-4 h-4 text-accent-deep" /> 已具备 ({res.matched_skills.filter((m: any) => m.importance === 'required').length})</div>
                    <div className="flex flex-wrap gap-1.5">
                      {res.matched_skills.filter((m: any) => m.importance === 'required').map((m: any) => (
                        <span key={m.name} className="chip border bg-accent/8 border-accent/30 text-accent-deep">
                          {m.name}{m.match_type === 'semantic' && <span className="text-[9px] opacity-70">语义</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div>
                     <div className="flex items-center gap-2 label mb-2"><XCircle className="w-4 h-4 text-rose-600" /> Top 关键能力缺口 ({topGaps.length})</div>
                     <div className="flex flex-wrap gap-1.5">
                       {topGaps.map((m: any) => (
                         <span key={m.name || m.skill} className="chip border bg-rose-50 border-rose-200 text-rose-700">{m.name || m.skill}</span>
                       ))}
                       {topGaps.length === 0 && <span className="text-xs text-accent-deep">核心必备能力已全覆盖</span>}
                     </div>
                  </div>
                </div>
              </Card>

              {result.learning_path?.length > 0 && (
                <Card className="p-5">
                  <div className="flex items-center gap-2 label mb-4"><IPath className="w-4 h-4 text-accent-deep" /> 岗位学习路径规划</div>
                  <div ref={pathRef} className="relative pl-6">
                    <div className="path-line absolute left-[7px] top-1 bottom-1 w-px bg-gradient-to-b from-accent to-accent/40" />
                    {result.learning_path.map((p: any) => (
                      <div key={p.step} data-reveal className="relative pb-4">
                        <span className="absolute -left-[19px] top-0.5 w-3.5 h-3.5 rounded-full bg-grad-accent ring-4 ring-white text-[8px] grid place-items-center font-bold text-slate-900">{p.step}</span>
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-slate-800">{p.skill}</span>
                          <Badge tone={p.priority === '高' ? 'rose' : 'amber'}>{p.priority}优先</Badge>
                          <Badge tone="slate">{p.category}</Badge>
                        </div>
                        {p.prerequisites?.length > 0 && (
                          <p className="text-[11px] text-slate-400 mt-0.5">先修：{p.prerequisites.join('、')}</p>
                        )}
                      </div>
                    ))}
                  </div>
                </Card>
              )}
              {auth.isAuthenticated && <div className="flex justify-end"><Link to="/history" className="btn-ghost"><History className="w-4 h-4" /> 查看已保存的匹配历史</Link></div>}

              {result.suggestions?.overall_advice && (
                <Card className="p-5">
                  <div className="flex items-center gap-2 label mb-3"><ILightbulb className="w-4 h-4 text-amber-600" /> 针对性改进建议</div>
                  <p className="text-sm text-slate-700 leading-relaxed">{result.suggestions.overall_advice}</p>
                  {result.suggestions.timeline && (
                    <div className="mt-2 text-xs text-accent-deep flex items-center gap-1"><Sparkles className="w-3 h-3" /> {result.suggestions.timeline}</div>
                  )}
                  {result.suggestions.skill_advice?.length > 0 && (
                    <div className="mt-3 space-y-2">
                      {result.suggestions.skill_advice.slice(0, 6).map((a: any, i: number) => (
                        <div key={i} className="rounded-lg bg-sky-50/70 px-3 py-2 text-xs">
                          <span className="text-slate-800 font-medium">{a.skill}</span>
                          <span className="text-slate-500"> — {a.action}</span>
                          {a.resource && <span className="text-slate-400"> · {a.resource}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
