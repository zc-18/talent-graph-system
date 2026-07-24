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
}

export const CATEGORY_COLORS: Record<string, string> = {
  人工智能: '#6366F1', 大数据: '#22D3EE', 物联网: '#34D399',
  智能系统: '#F59E0B', 云计算与工程: '#A855F7', 数据工程: '#F472B6', 其他: '#64748B',
}
