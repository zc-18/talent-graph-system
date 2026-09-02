import axios from 'axios'

export const AUTH_UNAUTHORIZED_EVENT = 'talent-graph:unauthorized'
const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
const http = axios.create({ baseURL: apiBase, timeout: 200000 })

let accessToken: string | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
}

/**
 * 动态数据挖掘·采集回放（SSE）。
 *
 * 必须写在这里、紧挨 setAccessToken：accessToken 是模块私有变量且只对外暴露 setter，
 * 只有同模块内的函数读得到它。EventSource 无法携带 Authorization 头，而 /mining 路由
 * 挂在 current_actor 后面，所以走 fetch + body.getReader() 手工分帧。
 *
 * 分帧逻辑与 components/ChatBot.tsx 完全一致，逐条都是有原因的：
 *   decode({stream:true}) —— 多字节汉字可能被切在两个 chunk 之间；
 *   buf.split('\n\n') + parts.pop() —— 末尾那截可能是半帧，留到下一轮再拼；
 *   剥掉 data: 前缀、跳过空行与 [DONE] —— 后者是终止哨兵而非 JSON。
 * signal 透传给 fetch：组件卸载时 abort，read() 抛 AbortError 由调用方吞掉。
 */
export async function miningReplay(
  runDate: string,
  onFrame: (frame: MiningReplayFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`
  const resp = await fetch(`${apiBase}/mining/runs/${encodeURIComponent(runDate)}/replay`, { headers, signal })
  if (!resp.ok) throw new Error(`回放接口返回 ${resp.status}`)
  if (!resp.body) throw new Error('回放接口未返回流式响应')
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const part of parts) {
      const line = part.replace(/^data:\s?/, '')
      if (!line || line === '[DONE]') continue
      try { onFrame(JSON.parse(line)) } catch { /* 半帧或心跳，忽略 */ }
    }
  }
}

http.interceptors.request.use(config => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`
  return config
})

http.interceptors.response.use(
  response => response,
  error => {
    if (error?.response?.status === 401) {
      accessToken = null
      window.dispatchEvent(new CustomEvent(AUTH_UNAUTHORIZED_EVENT))
    }
    return Promise.reject(error)
  },
)

// 统一提取后端/网络错误为可读文案（供 toast / ErrorState 使用）
export function errMsg(e: any, fallback = '操作失败，请稍后重试'): string {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message
  if (Array.isArray(detail)) return detail.map((item: any) => item?.msg).filter(Boolean).join('；') || fallback
  if (e?.response?.status === 401) return '会话已过期，请重新登录'
  if (e?.response?.status === 403) return '当前账号无权执行此操作'
  if (e?.code === 'ECONNABORTED') return '请求超时，请检查网络后重试'
  if (e?.message === 'Network Error') return '网络连接失败，请检查网络'
  return fallback
}

// ---------- 类型 ----------
export interface JobListItem {
  id: number; name: string; category: string; level: string; is_new: boolean
  confidence: number; evidence_count: number; emergence_score: number
  required_count: number; version: number; summary: string
  track?: string; industry?: string; recruitment_type?: string
  core_capabilities?: string[]
  confidence_as_of?: string | null; confidence_delta?: number | null
  contract_status?: 'ready' | 'evidence_insufficient' | string
  employer_count?: number
}
export interface Paginated<T> { items: T[]; total: number; page: number; size: number }
export type AppRole = 'user' | 'hr' | 'admin'
export interface AuthUser {
  id: number; username: string; role: AppRole; status: string
  organization_id?: number | null; organization_name?: string | null; permissions: string[]
  /** 后端未设置时用 username 兜底，前端可直接显示 */
  nickname?: string | null
  /** 为空时前端按 username 哈希取 /avatars/aNN.webp */
  avatar_url?: string | null
}
export interface AvatarPresets { items: string[]; total: number; max_upload_bytes: number }
export interface AvatarUploadResult { avatar_url: string; size: number; format: string; user: AuthUser }
export interface AuthResponse {
  access_token: string; token_type: 'bearer'; expires_at: string; user: AuthUser
}
export interface RoleContractSkill {
  name: string; parent?: string | null; granularity?: 'coarse' | 'fine'
  importance?: 'required' | 'bonus'; weight?: number; confidence?: number
  employer_count?: number; jd_support_count?: number
}
export interface RoleContractCluster {
  name: string; importance: 'required' | 'bonus'; weight: number; confidence: number
  level_required: string; support_ratio?: number; employer_count?: number
  skills: Array<string | RoleContractSkill>
}
export interface RoleContract {
  job_id: number; job_name: string; seniority: string; recruitment_type: string
  track: string; industry: string; evidence_window?: string; version: number; status: string
  clusters: RoleContractCluster[]
}
export interface JobVersion {
  id?: number; job_id: number; version: number; status: string; effective_at?: string | null
  evidence_window?: string | null; summary?: string; created_by?: number | null
  contract?: RoleContract; clusters?: RoleContractCluster[]; changes?: CapChange[]
}
export interface ConfidenceFactors {
  support: number; diversity: number; freshness: number; authority: number; external: number
}
export interface ConfidenceSnapshot {
  id?: number; run_id?: number; job_id?: number; computed_at?: string | null
  as_of?: string | null; created_at?: string | null; confidence?: number
  score?: number; score_before?: number | null; score_after?: number; delta?: number | null
  previous_confidence?: number | null; evidence_count?: number; valid_jd_count?: number
  factors?: ConfidenceFactors | null; job_version?: number
}
export interface Skill {
  id: number; skill_id: number; name: string; category: string; skill_type: string
  importance: string; weight: number; level_required: string; confidence: number
  source_count: number; status: string
  factors?: ConfidenceFactors | null
  parent_id?: number | null; parent_name?: string | null
  granularity?: 'coarse' | 'fine'
}
export interface EvidenceItem {
  type: string; snippet: string; url?: string | null; weight?: number
  source?: string | null; company?: string | null; publish_date?: string | null; job_title?: string | null
}
export interface AuthorityItem {
  kind: 'policy' | 'report'; title: string; issuer: string
  publish_date?: string | null; url?: string | null; excerpt?: string | null
}
export interface CapChange {
  version: number; change_type: 'add' | 'delete' | 'modify'; skill_name: string
  old_value?: any; new_value?: any; reason?: string; data_source?: any
  confidence?: number; created_at?: string
}
export interface LevelSkill {
  name: string; importance: string; weight: number; confidence: number
  factors?: ConfidenceFactors | null; level_required: string
}
export interface JobLevels {
  available: string[]
  levels: Record<string, { jd_count: number; skills: LevelSkill[] }>
}
export interface PipelineStats {
  funnel: { collected: number; after_dedup: number; parsed: number; validated_caps: number; filtered_caps: number; jobs: number; skills: number }
  capability_relations?: { active: number; candidate: number; deprecated: number }
  employer_validation?: { unique_employers: number; unknown_employer_jds: number; employer_validated_capabilities: number; candidate_capabilities: number }
  platforms: { platform: string; count: number; latest?: string | null }[]
  batches: { batch_key: string; platform: string; tier: string; kept: number; finished_at?: string | null }[]
  loop: { manual_edits: number; evolution_runs: number }
}
/* ---------- 动态数据挖掘（每日模拟聚合源 · 展示为 BOSS直聘）----------
   语料由竞赛主办方提供的离线模拟聚合源产出，不含雇主身份，
   因此其技能点一律以 candidate 态入图（见 gate_note）。 */
export interface MiningRunItem {
  run_date: string; status: string
  rows_read: number; rows_valid: number; rows_dedup: number; rows_mapped: number
  jobs_touched: number; new_skill_points: number; skills_created: number
  job_skills_created: number; evidence_created: number
  llm_calls: number; llm_cost_cny: number; llm_budget_hit: boolean; dry_run: boolean
  finished_at?: string | null
  /** 仅 /runs/{date} 详情返回 */
  shard_index?: number; cursor_start?: number; cursor_end?: number
  started_at?: string | null; error?: string | null
}
export interface MiningRunsResponse {
  source_label: string; platform: string; tier: string; enabled: boolean
  schedule: string; daily_budget_cny: number
  /** 新到旧 */
  items: MiningRunItem[]
}
export interface MiningFunnelStep {
  key: string; label: string
  /** in 是保留字级别的常见字段名，接口原样返回，这里不改名以免与后端对不齐 */
  in: number; out: number; dropped: number
  reasons: Record<string, number>
  detail?: string; duration_ms?: number; samples?: string[]
}
export type MiningDeltaType = 'new' | 'support_up' | 'support_down' | 'vanished'
export interface MiningTrainingStep {
  step: number; skill: string; category?: string; priority?: string; prerequisites?: string[]
}
export interface MiningSkillDelta {
  skill_name: string; delta_type: MiningDeltaType
  /**
   * 图谱节点 id 与「是否已入图」。挂不到任何粗粒度概念的技能词不会建 Skill 节点，
   * 只记录在观测层：此时 `skill_id === null` 且 `in_graph === false`（两者恒等价）。
   * 实测某日 672 条明细里 242 条如此，不是边缘情况，必须在界面上显式标出，
   * 并且**不能**给它们挂技能详情链接——背后没有行，点了就是 404。
   */
  skill_id?: number | null; in_graph?: boolean
  prev_support: number; curr_support: number
  prev_status?: string | null; curr_status?: string | null
  /** 公司领域数——不是雇主多样性，模拟源无雇主身份 */
  industry_count?: number; industries?: string[]; sample_titles?: string[]
  training_plan?: MiningTrainingStep[]
}
export interface MiningJobDelta {
  job_id: number; job_name: string; category?: string; rows: number
  new_count: number; support_up: number; support_down: number; vanished: number
  /** 后端按 MAX_DELTAS_PER_JOB 截断时给出真实总数，必须显示出来——静默砍掉等于伪造当日变化量 */
  truncated?: boolean; deltas_total?: number; deltas: MiningSkillDelta[]
}
export interface MiningTopSkill {
  name: string; count: number; job_id: number; job_name: string; delta_type: MiningDeltaType
  /** 与 MiningSkillDelta 同义：热点技能同样可能是「仅观测·未入图」的 */
  skill_id?: number | null; in_graph?: boolean
}
export interface MiningRunDetail {
  run: MiningRunItem; funnel: MiningFunnelStep[]; jobs: MiningJobDelta[]
  /** 后端按 MAX_JOBS 截断岗位块时给出的真实总数与标记（同上：截断必须说出来） */
  jobs_total?: number; jobs_truncated?: boolean
  top_skills: MiningTopSkill[]; gate_note: string
}
export interface MiningTrendItem {
  run_date: string; new_skill_points: number; rows_mapped: number
  skills_created: number; cumulative_new: number
}
export interface MiningJobDeltaHistoryItem {
  run_date: string; new_count: number; support_up: number; support_down: number
  vanished: number; truncated?: boolean; deltas_total?: number; deltas: MiningSkillDelta[]
}
export interface MiningJobDeltaHistory {
  job_id: number; job_name: string; items: MiningJobDeltaHistoryItem[]
}
/** SSE 回放帧。type 决定其余字段，故按判别联合的松散形式声明。 */
export interface MiningReplayFrame {
  type: 'start' | 'stage' | 'tick' | 'summary' | 'done'
  run_date?: string; total_stages?: number; source_label?: string
  index?: number; key?: string; label?: string; phase?: 'begin' | 'end'
  in?: number; out?: number; dropped?: number
  reasons?: Record<string, number>; detail?: string
  progress?: number; processed?: number; sample?: string
  new_skill_points?: number; jobs_touched?: number; skills_created?: number
}

export interface JobDetail {
  id: number; name: string; category: string; level: string; is_new: boolean
  summary: string; core_responsibilities: string[]; typical_scenarios: string[]
  required_skills: Skill[]; bonus_skills: Skill[]; confidence: number
  evidence_count: number; emergence_score: number; version: number; source_summary: any
  confidence_as_of?: string | null; confidence_delta?: number | null
  confidence_factors?: ConfidenceFactors | null; confidence_trend?: ConfidenceSnapshot[]
}
export interface GraphData {
  nodes: any[]; edges: any[]; stats: { jobs: number; skills: number; relations: number; capabilities?: number; mode?: string }
}
export interface Stats {
  total_jobs: number; new_jobs: number; total_skills: number; total_jds: number
  duplicate_jds: number; categories: Record<string, number>; avg_confidence: number
  confidence_as_of?: string | null; avg_confidence_delta?: number | null
  confidence_distribution?: Record<string, { count: number; ratio: number }>
  factor_averages?: ConfidenceFactors
  identified_employer_coverage?: number; identified_employer_jds?: number
  valid_jd_count?: number; valid_evidence_url_ratio?: number
  valid_evidence_url_count?: number; evidence_count?: number
}

export interface EvolutionTimelineSlice {
  year: number; label: string; start_at?: string | null; end_at?: string | null
  jd_count: number; employer_count: number; platforms: string[]
  valid_url_count: number; url_coverage: number
}
export interface EvolutionTimeline {
  job_id: number; job_name: string
  lifecycle_mode: 'first_observation' | 'historical_evolution'
  first_observed_at?: string | null; first_evidenced_at?: string | null
  first_published_at?: string | null; coverage_note: string
  corpus_slices: EvolutionTimelineSlice[]
  version_nodes: Array<{ id?: number | null; version: number; status: string
    effective_at?: string | null; summary?: string | null; evidence_window?: any
    change_count: number }>
  capability_changes: CapChange[]
  proposal_runs: Array<{ id: number; from_version: number; proposed_version: number
    status: string; created_at: string }>
}

export type DiscoveryRunStatus = 'queued' | 'running' | 'completed' | 'failed'
export interface DiscoveryRunRecord {
  id: number; query: string; conditions: Record<string, any>
  classification: string | null; matched_job_id: number | null
  status: DiscoveryRunStatus; error: string | null
  signals: Record<string, any>; evidence: any[]; created_at: string
}
/** `GET /discovery/runs/{id}` 与同步 POST 返回同一个信封，前端只写一套渲染。 */
export interface DiscoveryRunResult {
  idempotent_replay: boolean; run_id: number
  status: DiscoveryRunStatus; error: string | null
  classification: string | null; candidate_id: number | null
  run: DiscoveryRunRecord; candidate: any; matched_job: any
  evolution_run_id: number | null
}

export interface MatchHistoryItem {
  id: number; status: string; created_at: string; job_id: number; job_name: string
  job_version: number; overall_score: number; level?: string; top_gaps?: any[]
}
export interface DiscoveryCandidate {
  id: number; status: string; created_at: string; owner_id?: number; organization_id?: number | null
  title?: string; job_title?: string; current_revision?: any; current_revision_number?: number; definition?: any; revisions?: any[]; reviews?: any[]
  published_job_id?: number | null
}
export interface RecruitmentProgress { total: number; processed: number; succeeded: number; failed: number }
export interface RecruitmentBatch {
  id: number; status: string; created_at: string; name?: string; job_id: number; job_name?: string
  job_version?: number; retention_days?: number; progress: RecruitmentProgress; failures?: any[]
}
export interface RankingItem {
  rank: number; candidate_id: number; code: string; overall_score: number
  dimension_scores: Record<string, number>; status: string
}
export interface FeedbackTicket {
  id: number; status: string; created_at: string; type?: string; subject?: string
  content?: string; category?: string; target_type?: string; target_id?: number | string | null; updated_at?: string
  current_revision?: number; evidence?: any[]; owner_user_id?: number; owner_username?: string
  organization_id?: number | null; organization_name?: string | null
  applied_record_type?: string | null; applied_record_id?: string | null
}
export interface EvolutionRunItem {
  id: number; job_id: number; job_name?: string | null; from_version: number
  proposed_version: number; status: string; stats: Record<string, number>
  error?: string | null; created_at: string; updated_at: string
  input_snapshot?: any; proposed_snapshot?: any; diff?: CapChange[]
  reviews?: Array<{ id: number; action: string; comment?: string | null; reviewer_id: number; created_at: string }>
}
export interface DailyUsage {
  date: string; active_users?: number; logins?: number; job_views?: number; discovery_runs?: number
  matches?: number; batch_resumes?: number; team_reviews?: number; error_rate?: number
  p50_ms?: number; p95_ms?: number; feature?: string; calls?: number; success_rate?: number
}

// ---------- 接口 ----------
// ---------- 人才侧（意见⑧）----------
export interface ResumeBatchItem {
  batch_key: string; source_type: string; source_name: string; source_url: string
  license: string; tier: string; authority: number; method: string; robots_ok: boolean
  collected: number; kept: number; profiles: number; raw_dir: string; notes: string
}
export interface TalentCorpus {
  total_profiles: number; total_skills_extracted: number; holdout: number
  by_source: Record<string, number>; by_language: Record<string, number>
  by_cluster: Record<string, number>
  alias_accepted: number; alias_rejected: number
  batches: ResumeBatchItem[]; privacy_notice: string
}
export interface TalentProfile {
  id: number; code: string; source_type: string; source_name: string; source_url: string
  license: string; language: string; target_cluster: string | null
  matched_job_id: number | null; matched_job_name: string | null
  years_experience: number; education: string; skill_count: number; skills: string[]
  text_len: number; quality_score: number; holdout: boolean
}
export interface SupplyDemandItem {
  skill: string; category: string; importance: string; weight: number; confidence: number
  supply_count: number; supply_rate: number
  aligned_supply_count: number; aligned_supply_rate: number; gap: number
}
export interface SupplyDemand {
  job: { id: number; name: string; category: string }
  corpus_size: number; aligned_talents: number
  required_total: number; required_covered: number; coverage_rate: number
  items: SupplyDemandItem[]; note: string
}
export interface TeamItem {
  id: number; name: string; description: string; target_job_id?: number; size: number
  /** null = 公共演示团队（所有人可见、只读）；有值 = 组织私有团队 */
  organization_id?: number | null
  /** 当前登录身份能否改动成员；false 时前端要禁用写操作而不是让人点了吃错误码 */
  editable?: boolean
}
export interface TeamEvent {
  id: number; action: string; member_id: number | null; details: Record<string, any>
  before: { member_count: number; coverage_rate: number | null } | null
  after: { member_count: number; coverage_rate: number | null } | null
  created_at: string
}
export interface GapSkill {
  skill: string; category: string; weight: number; confidence: number
  holders: { member_id: number; display_name: string; talent_code: string | null }[]
}
export interface Contribution {
  member_id: number; display_name: string; role_label: string | null
  talent_code: string | null; talent_id: number; skill_count: number
  covers_required: number; uniquely_covers: number; unique_skills: string[]
}
export interface TeamGap {
  team: { id: number; name: string; size: number }
  job: { id: number; name: string; category: string }
  required_total: number; required_covered: number; coverage_rate: number
  weighted_coverage: number; bonus_total: number; bonus_covered: number
  covered: GapSkill[]; missing: GapSkill[]; contributions: Contribution[]
}
export interface AliasItem {
  alias: string; canonical: string | null; status: string; talent_count: number
  confidence: number; reason: string | null; skill_id: number | null
}

/** 门户首屏公开数据条。字段与 backend/app/routers/public.py 的 _PUBLIC_FIELDS 白名单一一对应，
    未登录可访问；/graph/stats 仍然需要登录，没有被放开。 */
export interface PublicStats {
  total_jobs: number; new_jobs: number; total_skills: number; total_jds: number
  avg_confidence: number; identified_employer_coverage: number; evidence_count: number
}

export const api = {
  health: () => http.get<{ status: string; read_only?: boolean }>('/health').then(r => r.data),
  stats: () => http.get<Stats>('/graph/stats').then(r => r.data),
  publicStats: () => http.get<PublicStats>('/public/stats').then(r => r.data),
  categories: () => http.get<{ categories: string[]; levels: string[] }>('/graph/categories').then(r => r.data),
  panorama: (category?: string, level?: string, minConf = 0, mode: 'job' | 'capability' | 'skill' = 'skill', track?: string, recruitmentType?: string) =>
    http.get<GraphData>('/graph/panorama', { params: { category, level, min_confidence: minConf, mode, track, recruitment_type: recruitmentType } }).then(r => r.data),
  skillTree: () => http.get('/graph/skill-tree').then(r => r.data),
  skillDetail: (id: number) => http.get(`/graph/skill/${id}`).then(r => r.data),

  jobs: (params: any = {}) => http.get<Paginated<JobListItem>>('/jobs', { params }).then(r => r.data),
  job: (id: number) => http.get<JobDetail>(`/jobs/${id}`).then(r => r.data),
  jobContract: (id: number) => http.get<RoleContract>(`/jobs/${id}/contract`).then(r => r.data),
  jobVersions: (id: number) => http.get<{ items: JobVersion[] } | Paginated<JobVersion>>(`/jobs/${id}/versions`).then(r => r.data),
  jobEvidence: (id: number) => http.get(`/jobs/${id}/evidence`).then(r => r.data),
  jobAuthority: (id: number) => http.get<{ items: AuthorityItem[] }>(`/jobs/${id}/authority`).then(r => r.data),
  jobConfidenceHistory: (id: number) => http.get<{ items: ConfidenceSnapshot[] } | ConfidenceSnapshot[]>(`/jobs/${id}/confidence-history`)
    .then(r => Array.isArray(r.data) ? r.data : r.data.items || []),
  seeds: () => http.get<{ seeds: string[] }>('/discovery/seeds').then(r => r.data),
  // async=true 时后端只建 queued 行就返回，检索与大模型挪到后台任务，随后轮询
  // discoveryRunResult 取结果——同步跑最坏要 200 秒，够浏览器和网关各超时一次。
  discoveryRun: (body: any, options: { async?: boolean } = {}) =>
    http.post<DiscoveryRunResult>('/discovery/runs', body,
      options.async ? { params: { async_mode: true } } : undefined).then(r => r.data),
  discoveryRuns: (params: any = {}) =>
    http.get<Paginated<DiscoveryRunRecord>>('/discovery/runs', { params }).then(r => r.data),
  discoveryRunResult: (id: number) =>
    http.get<DiscoveryRunResult>(`/discovery/runs/${id}`).then(r => r.data),
  discoveryCandidates: (params: any = {}) => http.get<Paginated<DiscoveryCandidate>>('/discovery/candidates', { params }).then(r => r.data),
  discoveryCandidate: (id: number) => http.get<DiscoveryCandidate>(`/discovery/candidates/${id}`).then(r => r.data),
  updateDiscoveryCandidate: (id: number, body: any) => http.patch<DiscoveryCandidate>(`/discovery/candidates/${id}`, body).then(r => r.data),
  submitDiscoveryCandidate: (id: number) => http.post<DiscoveryCandidate>(`/discovery/candidates/${id}/submit`).then(r => r.data),

  changes: (jobId: number) => http.get(`/evolution/${jobId}/changes`).then(r => r.data),
  evolutionTimeline: (jobId: number) => http.get<EvolutionTimeline>(`/evolution/${jobId}/timeline`).then(r => r.data),
  jobLevels: (jobId: number) => http.get<JobLevels>(`/evolution/${jobId}/levels`).then(r => r.data),
  levelDiff: (jobId: number, frm: string, to: string) =>
    http.get<{ from: string; to: string; from_label: string; to_label: string; changes: CapChange[] }>(
      `/evolution/${jobId}/level-diff`, { params: { frm, to } }).then(r => r.data),
  pipelineStats: () => http.get<PipelineStats>('/graph/pipeline-stats').then(r => r.data),

  // ---------- 动态数据挖掘（每日模拟聚合源）----------
  // 回放是 SSE，走本文件顶部的 miningReplay（fetch + reader），不在这里。
  miningRuns: (limit = 30) => http.get<MiningRunsResponse>('/mining/runs', { params: { limit } }).then(r => r.data),
  /** runDate 可以传字面量 'latest' */
  miningRun: (runDate: string) => http.get<MiningRunDetail>(`/mining/runs/${encodeURIComponent(runDate)}`).then(r => r.data),
  miningSkillTrend: (days = 30) => http.get<{ items: MiningTrendItem[] }>('/mining/skill-trend', { params: { days } }).then(r => r.data),
  miningJobDeltas: (jobId: number, limit = 30) =>
    http.get<MiningJobDeltaHistory>(`/mining/jobs/${jobId}/deltas`, { params: { limit } }).then(r => r.data),

  previewEvolution: (jobId: number, newJds: string[], useWeb = true) =>
    http.post('/evolution/update', { job_id: jobId, new_jds: newJds, use_web: useWeb }).then(r => r.data),

  analyze: (body: any) => http.post('/match/analyze', body).then(r => r.data),
  uploadResume: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return http.post('/match/resume/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
  },
  matchHistory: (params: any = {}) => http.get<Paginated<MatchHistoryItem>>('/me/matches', { params }).then(r => r.data),
  matchHistoryDetail: (id: number) => http.get(`/me/matches/${id}`).then(r => r.data),

  register: (body: { username: string; password: string; role?: 'user' | 'hr'; organization_name?: string }) =>
    http.post<AuthResponse>('/auth/register', body).then(r => r.data),
  login: (body: { username: string; password: string }) => http.post<AuthResponse>('/auth/login', body).then(r => r.data),
  logout: () => http.post('/auth/logout').then(r => r.data),
  me: () => http.get<AuthUser>('/auth/me').then(r => r.data),
  // 账号资料：PATCH 的返回体与 /auth/me 同构，拿到就能整体替换本地 user，不用二次拉取
  avatarPresets: () => http.get<AvatarPresets>('/me/avatar-presets').then(r => r.data),
  updateProfile: (body: { nickname?: string; avatar_url?: string }) =>
    http.patch<AuthUser>('/me/profile', body).then(r => r.data),
  uploadAvatar: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post<AvatarUploadResult>('/me/avatar', fd,
      { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
  },

  feedback: (body: any) => http.post<FeedbackTicket>('/feedback', body).then(r => r.data),
  feedbackList: (params: any = {}) => http.get<Paginated<FeedbackTicket>>('/feedback', { params }).then(r => r.data),
  feedbackDetail: (id: number) => http.get<FeedbackTicket>(`/feedback/${id}`).then(r => r.data),

  recruitmentBatches: (params: any = {}) => http.get<Paginated<RecruitmentBatch>>('/hr/recruitment-batches', { params }).then(r => r.data),
  createRecruitmentBatch: (body: any) => http.post<RecruitmentBatch>('/hr/recruitment-batches', body).then(r => r.data),
  recruitmentBatch: (id: number) => http.get<RecruitmentBatch>(`/hr/recruitment-batches/${id}`).then(r => r.data),
  uploadRecruitmentFiles: (id: number, files: File[], authorizationConfirmed: boolean, retentionDays: number) => {
    const fd = new FormData()
    files.forEach(file => fd.append('files', file))
    fd.append('authorization_confirmed', String(authorizationConfirmed))
    fd.append('retention_days', String(retentionDays))
    return http.post<RecruitmentBatch>(`/hr/recruitment-batches/${id}/files`, fd).then(r => r.data)
  },
  recruitmentRanking: (id: number, params: any = {}) =>
    http.get<Paginated<RankingItem>>(`/hr/recruitment-batches/${id}/ranking`, { params }).then(r => r.data),
  selectRecruitmentCandidates: (id: number, body: { candidate_ids: number[]; team_id?: number | null }) =>
    http.post(`/hr/recruitment-batches/${id}/select`, body).then(r => r.data),

  adminCandidates: (params: any = {}) => http.get<Paginated<DiscoveryCandidate>>('/discovery/candidates', { params }).then(r => r.data),
  reviewCandidate: (id: number, body: { action: 'approve' | 'reject'; comment: string; publish: boolean }) =>
    http.post(`/admin/candidates/${id}/review`, body).then(r => r.data),
  adminUsers: (params: any = {}) => http.get<Paginated<any>>('/admin/users', { params }).then(r => r.data),
  adminOrganizations: (params: any = {}) => http.get<Paginated<any>>('/admin/organizations', { params }).then(r => r.data),
  adminAuditLogs: (params: any = {}) => http.get<Paginated<any>>('/admin/audit-logs', { params }).then(r => r.data),
  adminUsageDaily: (params: any = {}) => http.get<Paginated<DailyUsage> | { items: DailyUsage[] }>('/admin/usage/daily', { params }).then(r => r.data),
  adminFeedback: (params: any = {}) => http.get<Paginated<FeedbackTicket>>('/admin/feedback', { params }).then(r => r.data),
  reviewFeedback: (id: number, body: { action: 'triage' | 'approve' | 'reject' | 'apply'; comment?: string; applied_record_type?: string; applied_record_id?: string }) =>
    http.post(`/admin/feedback/${id}/review`, body).then(r => r.data),
  adminEvolutionRuns: (params: any = {}) => http.get<Paginated<EvolutionRunItem>>('/admin/evolution-runs', { params }).then(r => r.data),
  adminEvolutionRun: (id: number) => http.get<EvolutionRunItem>(`/admin/evolution-runs/${id}`).then(r => r.data),
  createEvolutionRun: (body: { job_id: number; evidence_batch?: any; proposed_snapshot?: any; idempotency_key?: string }) =>
    http.post<{ idempotent_replay: boolean; run: EvolutionRunItem }>('/admin/evolution-runs', body).then(r => r.data),
  proposeEvolutionRun: (id: number, body: { evidence_batch?: any; proposed_snapshot?: any } = {}) =>
    http.post<EvolutionRunItem>(`/admin/evolution-runs/${id}/propose`, body).then(r => r.data),
  reviewEvolutionRun: (id: number, body: { action: 'approve' | 'reject'; comment?: string }) =>
    http.post<EvolutionRunItem>(`/admin/evolution-runs/${id}/review`, body).then(r => r.data),
  publishEvolutionRun: (id: number) => http.post(`/admin/evolution-runs/${id}/publish`).then(r => r.data),

  // ---------- 人才与团队盘点（意见⑧）----------
  talentCorpus: () => http.get<TalentCorpus>('/talent/corpus').then(r => r.data),
  talentProfiles: (params: any = {}) =>
    http.get<{ total: number; items: TalentProfile[] }>('/talent/profiles', { params }).then(r => r.data),
  supplyDemand: (jobId: number) =>
    http.get<SupplyDemand>('/talent/supply-demand', { params: { job_id: jobId } }).then(r => r.data),
  teams: () => http.get<{ items: TeamItem[] }>('/talent/teams', { params: { page: 1, size: 100 } }).then(r => r.data),
  createTeam: (body: { name: string; description?: string; target_job_id: number }) =>
    http.post<TeamItem>('/talent/teams', body).then(r => r.data),
  teamDetail: (id: number) => http.get('/talent/teams/' + id).then(r => r.data),
  teamHistory: (id: number) => http.get<{ items: TeamEvent[] }>(`/talent/teams/${id}/history`, { params: { page: 1, size: 20 } }).then(r => r.data),
  addTeamMember: (id: number, body: { resume_profile_id: number; display_name: string; role_label?: string }) =>
    http.post(`/talent/teams/${id}/members`, body).then(r => r.data),
  removeTeamMember: (teamId: number, memberId: number) =>
    http.delete(`/talent/teams/${teamId}/members/${memberId}`).then(r => r.data),
  teamGap: (teamId: number, jobId: number) =>
    http.get<TeamGap>(`/talent/teams/${teamId}/gap`, { params: { job_id: jobId } }).then(r => r.data),
  talentAliases: (status = 'accepted', limit = 200) =>
    http.get<{ total: number; items: AliasItem[] }>('/talent/aliases', { params: { status, limit } }).then(r => r.data),
  uploadTeamResume: (teamId: number, file: File, displayName: string, roleLabel: string, cluster: string,
                     authorizationConfirmed: boolean, retentionDays = 90) => {
    const fd = new FormData()
    fd.append('file', file); fd.append('display_name', displayName)
    fd.append('role_label', roleLabel); fd.append('target_cluster', cluster)
    fd.append('authorization_confirmed', String(authorizationConfirmed))
    fd.append('retention_days', String(retentionDays))
    return http.post(`/talent/teams/${teamId}/members/upload`, fd,
      { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
  },
}

// 岗位领域 + 技能分类共用一张色表。后两项（编程语言 / 数据库与存储）只出现在技能侧，
// 岗位不会用到——技能分类与岗位领域是两个集合，见后端 taxonomy.CATEGORIES 的说明。
export const CATEGORY_COLORS: Record<string, string> = {
  // 用户明确否掉了青/绿系（墨青、teal、emerald）。整张表改为「蓝为主轴 + 紫/琥珀/玫红
  // 做区分」，全表不含任何绿或青绿色，同时保持相邻项在力导图里仍能一眼分开。
  人工智能: '#3B82F6', 大数据: '#0EA5E9', 物联网: '#7A6BD8',
  智能系统: '#F59E0B', 云计算与工程: '#A855F7', 数据工程: '#F472B6', 其他: '#64748B',
  编程语言: '#38BDF8', 数据库与存储: '#4F46E5',
}
