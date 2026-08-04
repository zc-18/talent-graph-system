import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 200000 })

// 统一提取后端/网络错误为可读文案（供 toast / ErrorState 使用）
export function errMsg(e: any, fallback = '操作失败，请稍后重试'): string {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (e?.code === 'ECONNABORTED') return '请求超时，请检查网络后重试'
  if (e?.message === 'Network Error') return '网络连接失败，请检查网络'
  return fallback
}

// ---------- 类型 ----------
export interface JobListItem {
  id: number; name: string; category: string; level: string; is_new: boolean
  confidence: number; evidence_count: number; emergence_score: number
  required_count: number; version: number; summary: string
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
  nodes: any[]; edges: any[]; stats: { jobs: number; skills: number; relations: number }
}
export interface Stats {
  total_jobs: number; new_jobs: number; total_skills: number; total_jds: number
  duplicate_jds: number; categories: Record<string, number>; avg_confidence: number
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
export interface TeamItem { id: number; name: string; description: string; size: number }
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
  stats: () => http.get<Stats>('/graph/stats').then(r => r.data),
  categories: () => http.get<{ categories: string[]; levels: string[] }>('/graph/categories').then(r => r.data),
  panorama: (category?: string, level?: string, minConf = 0) =>
    http.get<GraphData>('/graph/panorama', { params: { category, level, min_confidence: minConf } }).then(r => r.data),
  skillTree: () => http.get('/graph/skill-tree').then(r => r.data),
  skillDetail: (id: number) => http.get(`/graph/skill/${id}`).then(r => r.data),

  jobs: (params: any = {}) => http.get<{ total: number; items: JobListItem[] }>('/jobs', { params }).then(r => r.data),
  job: (id: number) => http.get<JobDetail>(`/jobs/${id}`).then(r => r.data),
  jobEvidence: (id: number) => http.get(`/jobs/${id}/evidence`).then(r => r.data),
  jobAuthority: (id: number) => http.get<{ items: AuthorityItem[] }>(`/jobs/${id}/authority`).then(r => r.data),
  createJob: (body: any) => http.post('/jobs', body).then(r => r.data),
  manualEdit: (body: any) => http.post('/jobs/manual-edit', body).then(r => r.data),
  deleteJob: (id: number) => http.delete(`/jobs/${id}`).then(r => r.data),

  seeds: () => http.get<{ seeds: string[] }>('/discovery/seeds').then(r => r.data),
  discover: (keyword: string, save = false) =>
    http.post('/discovery/discover', { keyword, save }).then(r => r.data),

  changes: (jobId: number) => http.get(`/evolution/${jobId}/changes`).then(r => r.data),
  jobLevels: (jobId: number) => http.get<JobLevels>(`/evolution/${jobId}/levels`).then(r => r.data),
  levelDiff: (jobId: number, frm: string, to: string) =>
    http.get<{ from: string; to: string; from_label: string; to_label: string; changes: CapChange[] }>(
      `/evolution/${jobId}/level-diff`, { params: { frm, to } }).then(r => r.data),
  pipelineStats: () => http.get<PipelineStats>('/graph/pipeline-stats').then(r => r.data),
  evolve: (jobId: number, newJds: string[], useWeb = true) =>
    http.post('/evolution/update', { job_id: jobId, new_jds: newJds, use_web: useWeb }).then(r => r.data),

  analyze: (body: any) => http.post('/match/analyze', body).then(r => r.data),
  uploadResume: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return http.post('/match/resume/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
  },

  // ---------- 人才与团队盘点（意见⑧）----------
  talentCorpus: () => http.get<TalentCorpus>('/talent/corpus').then(r => r.data),
  talentProfiles: (params: any = {}) =>
    http.get<{ total: number; items: TalentProfile[] }>('/talent/profiles', { params }).then(r => r.data),
  supplyDemand: (jobId: number) =>
    http.get<SupplyDemand>('/talent/supply-demand', { params: { job_id: jobId } }).then(r => r.data),
  teams: () => http.get<{ items: TeamItem[] }>('/talent/teams').then(r => r.data),
  teamDetail: (id: number) => http.get('/talent/teams/' + id).then(r => r.data),
  teamGap: (teamId: number, jobId: number) =>
    http.get<TeamGap>(`/talent/teams/${teamId}/gap`, { params: { job_id: jobId } }).then(r => r.data),
  talentAliases: (status = 'accepted', limit = 200) =>
    http.get<{ total: number; items: AliasItem[] }>('/talent/aliases', { params: { status, limit } }).then(r => r.data),
  uploadTeamResume: (teamId: number, file: File, displayName: string, roleLabel: string, cluster: string) => {
    const fd = new FormData()
    fd.append('file', file); fd.append('display_name', displayName)
    fd.append('role_label', roleLabel); fd.append('target_cluster', cluster)
    return http.post(`/talent/teams/${teamId}/members/upload`, fd,
      { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
  },
}

export const CATEGORY_COLORS: Record<string, string> = {
  人工智能: '#6366F1', 大数据: '#22D3EE', 物联网: '#34D399',
  智能系统: '#F59E0B', 云计算与工程: '#A855F7', 数据工程: '#F472B6', 其他: '#64748B',
}
