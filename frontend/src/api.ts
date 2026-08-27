import axios from 'axios'

export const AUTH_UNAUTHORIZED_EVENT = 'talent-graph:unauthorized'
const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
const http = axios.create({ baseURL: apiBase, timeout: 200000 })

let accessToken: string | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
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
}
export interface Paginated<T> { items: T[]; total: number; page: number; size: number }
export type AppRole = 'user' | 'hr' | 'admin'
export interface AuthUser {
  id: number; username: string; role: AppRole; status: string
  organization_id?: number | null; organization_name?: string | null; permissions: string[]
}
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
export interface JobDetail {
  id: number; name: string; category: string; level: string; is_new: boolean
  summary: string; core_responsibilities: string[]; typical_scenarios: string[]
  required_skills: Skill[]; bonus_skills: Skill[]; confidence: number
  evidence_count: number; emergence_score: number; version: number; source_summary: any
}
export interface GraphData {
  nodes: any[]; edges: any[]; stats: { jobs: number; skills: number; relations: number; capabilities?: number; mode?: string }
}
export interface Stats {
  total_jobs: number; new_jobs: number; total_skills: number; total_jds: number
  duplicate_jds: number; categories: Record<string, number>; avg_confidence: number
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
export interface TeamItem { id: number; name: string; description: string; target_job_id?: number; size: number }
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

export const api = {
  health: () => http.get<{ status: string; read_only?: boolean }>('/health').then(r => r.data),
  stats: () => http.get<Stats>('/graph/stats').then(r => r.data),
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
  seeds: () => http.get<{ seeds: string[] }>('/discovery/seeds').then(r => r.data),
  discover: (keyword: string, save = false) =>
    http.post('/discovery/discover', { keyword, save }).then(r => r.data),
  discoveryRun: (body: any) => http.post('/discovery/runs', body).then(r => r.data),
  discoveryCandidates: (params: any = {}) => http.get<Paginated<DiscoveryCandidate>>('/discovery/candidates', { params }).then(r => r.data),
  discoveryCandidate: (id: number) => http.get<DiscoveryCandidate>(`/discovery/candidates/${id}`).then(r => r.data),
  updateDiscoveryCandidate: (id: number, body: any) => http.patch<DiscoveryCandidate>(`/discovery/candidates/${id}`, body).then(r => r.data),
  submitDiscoveryCandidate: (id: number) => http.post<DiscoveryCandidate>(`/discovery/candidates/${id}/submit`).then(r => r.data),

  changes: (jobId: number) => http.get(`/evolution/${jobId}/changes`).then(r => r.data),
  jobLevels: (jobId: number) => http.get<JobLevels>(`/evolution/${jobId}/levels`).then(r => r.data),
  levelDiff: (jobId: number, frm: string, to: string) =>
    http.get<{ from: string; to: string; from_label: string; to_label: string; changes: CapChange[] }>(
      `/evolution/${jobId}/level-diff`, { params: { frm, to } }).then(r => r.data),
  pipelineStats: () => http.get<PipelineStats>('/graph/pipeline-stats').then(r => r.data),
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
  人工智能: '#6366F1', 大数据: '#22D3EE', 物联网: '#34D399',
  智能系统: '#F59E0B', 云计算与工程: '#A855F7', 数据工程: '#F472B6', 其他: '#64748B',
  编程语言: '#0EA5E9', 数据库与存储: '#14B8A6',
}
