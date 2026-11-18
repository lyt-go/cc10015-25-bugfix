/** 样本分群成员确认结果：列表页与详情页共用同一份接口，保证两边读数一致。 */
import { request } from '@/api/client'

export type MemberSummary = {
  group_id?: number
  state: string
  state_label?: string
  ready?: boolean
  reason?: string
  reason_code?: string
  retryable?: boolean
  sample_base?: number | null
  member_limit?: number | null
  matched_count?: number
  member_count?: number
  unexpanded_count?: number
  truncated?: boolean
  snapshot_id?: string | null
  version_tag?: string
  computed_at?: number
}

export type Member = {
  member_key: string
  user_id: string
  group_type: string
  metric_value: number
}

export type MemberSnapshot = MemberSummary & { members: Member[] }

const ENDPOINT = '/api/sample_group'

async function readJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(payload.detail || `接口返回 ${response.status}，成员明细未更新`)
  }
  return payload as T
}

export function fetchMemberSummary(groupId: number): Promise<MemberSummary> {
  return readJson<MemberSummary>(`${ENDPOINT}/${groupId}/members`)
}

export function fetchMemberSnapshot(groupId: number, force = false): Promise<MemberSnapshot> {
  return readJson<MemberSnapshot>(`${ENDPOINT}/${groupId}/members`, { method: force ? 'POST' : 'GET' })
}

export function fetchMemberAudit(groupId: number): Promise<Record<string, unknown>> {
  return readJson<Record<string, unknown>>(`${ENDPOINT}/${groupId}/members/audit`)
}

export function describeDifference(summary: MemberSummary): string {
  const base = summary.sample_base
  const matched = summary.matched_count ?? 0
  const shown = summary.member_count ?? 0
  const unexpanded = summary.unexpanded_count ?? 0
  const limit = summary.member_limit ?? 0
  if (base === null || base === undefined) {
    return `样本基数缺失，命中 ${matched} 人，明细 ${shown} 条；差异无法用基数解释。`
  }
  if (summary.state === 'truncated') {
    return `样本基数 ${base}，命中 ${matched} 人；按成员上限 ${limit} 展示 ${shown} 条，另有 ${unexpanded} 条未展开。`
  }
  if (summary.state === 'empty') {
    return `样本基数 ${base}，筛选条件命中 0 人；明细为空是筛选结果，不代表样本总量为 0。`
  }
  if (summary.state === 'ready') {
    return `样本基数 ${base}，命中 ${matched} 人，明细 ${shown} 条；成员数与样本基数的差异来自筛选条件。`
  }
  return `样本基数 ${base}，命中 ${matched} 人，明细 ${shown} 条；当前结果不可作为正式成员明细使用。`
}
