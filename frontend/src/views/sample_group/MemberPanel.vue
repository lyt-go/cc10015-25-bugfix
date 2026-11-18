<template>
  <section class="member-panel" :data-testid="`member-panel-${summary.group_id ?? groupId}`" :data-state="summary.state">
    <header class="member-head">
      <div>
        <h4>分群成员明细</h4>
        <p class="page-desc">
          {{ summary.state_label || '未知' }} · {{ summary.reason || '暂无说明' }}
        </p>
      </div>
      <div class="member-actions">
        <button class="btn" type="button" data-testid="refresh-members" :disabled="loading" @click="reload(true)">
          {{ loading ? '计算中…' : '重新计算' }}
        </button>
        <button
          v-if="summary.retryable"
          class="btn primary"
          type="button"
          data-testid="retry-members"
          :disabled="loading"
          @click="reload(true)"
        >
          重试
        </button>
      </div>
    </header>

    <div class="stat-row member-stats">
      <article class="stat-card">
        <span class="stat-label">状态</span>
        <strong class="stat-value" data-testid="member-state">{{ summary.state_label || '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">样本基数</span>
        <strong class="stat-value" data-testid="member-base">{{ formatNumber(summary.sample_base) }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">命中成员</span>
        <strong class="stat-value" data-testid="member-matched">{{ formatNumber(summary.matched_count) }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">明细条数</span>
        <strong class="stat-value" data-testid="member-count">{{ formatNumber(summary.member_count) }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">未展开</span>
        <strong class="stat-value" data-testid="member-unexpanded">{{ formatNumber(summary.unexpanded_count) }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">成员上限</span>
        <strong class="stat-value" data-testid="member-limit">{{ formatNumber(summary.member_limit) }}</strong>
      </article>
    </div>

    <p class="member-note" data-testid="member-diff">
      {{ differenceNote }}
    </p>

    <p v-if="errorMessage" class="error-text" data-testid="member-error">{{ errorMessage }}</p>
    <p v-if="errorMessage && staleSummary" class="member-note" data-testid="member-stale">
      上次确认结果：{{ staleSummary.state_label }}，明细 {{ staleSummary.member_count ?? 0 }} 条，
      样本基数 {{ formatNumber(staleSummary.sample_base) }}；本次失败不影响该份结果。
    </p>

    <table v-if="members.length" class="data-table">
      <thead>
        <tr><th>成员键</th><th>用户编号</th><th>分组</th><th>指标值</th></tr>
      </thead>
      <tbody>
        <tr
          v-for="member in visibleMembers"
          :key="String(member.member_key)"
          data-testid="member-row"
          :data-member-key="String(member.member_key)"
        >
          <td>{{ member.member_key }}</td>
          <td>{{ member.user_id }}</td>
          <td>{{ member.group_type }}</td>
          <td>{{ member.metric_value }}</td>
        </tr>
        <tr v-if="members.length > displayLimit">
          <td colspan="4" class="empty-state">
            已展示前 {{ displayLimit }} 条，另有 {{ members.length - displayLimit }} 条可直接展开核对
            <button class="link" type="button" data-testid="expand-members" @click="expanded = true">
              展开全部
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty-state" data-testid="member-empty">{{ emptyText }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

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
}

type Member = {
  member_key: string
  user_id: string
  group_type: string
  metric_value: number
}

const props = withDefaults(
  defineProps<{
    groupId: number
    initial?: MemberSummary | null
    compact?: boolean
  }>(),
  { initial: null, compact: false },
)

const ENDPOINT = '/api/sample_group'
const DISPLAY_LIMIT = 20

const summary = ref<MemberSummary>(props.initial ?? { state: 'failed', state_label: '计算失败', reason: '尚未读取成员明细' })
const members = ref<Member[]>([])
const loading = ref(false)
const errorMessage = ref('')
const expanded = ref(false)
const staleSummary = ref<MemberSummary | null>(null)

watch(
  () => props.initial,
  (value) => { if (value) summary.value = value },
)

const displayLimit = computed(() => (expanded.value ? members.value.length : DISPLAY_LIMIT))
const visibleMembers = computed(() => members.value.slice(0, displayLimit.value))

const emptyText = computed(() => {
  if (summary.value.state === 'empty') return '空群：筛选条件命中 0 名成员，成员明细为空'
  if (summary.value.state === 'no_data') return '暂无数据：缺少有效样本基数，尚未生成成员明细'
  if (summary.value.state === 'invalid') return '筛选条件异常：未生成成员明细，请先修正筛选条件'
  if (summary.value.state === 'failed') return '计算失败：成员明细不可用，可重试或查看最近完整成员集合'
  return '暂无成员明细'
})

const differenceNote = computed(() => {
  const base = summary.value.sample_base
  const matched = summary.value.matched_count ?? 0
  const shown = summary.value.member_count ?? 0
  const unexpanded = summary.value.unexpanded_count ?? 0
  const limit = summary.value.member_limit ?? DISPLAY_LIMIT
  if (base === null || base === undefined) {
    return `样本基数缺失，命中 ${matched} 人，明细 ${shown} 条；差异无法用基数解释。`
  }
  if (summary.value.state === 'truncated') {
    return `样本基数 ${base}，命中 ${matched} 人；按成员上限 ${limit} 展示 ${shown} 条，另有 ${unexpanded} 条未展开。`
  }
  if (summary.value.state === 'empty') {
    return `样本基数 ${base}，筛选条件命中 0 人；明细为空是筛选结果，不代表样本总量为 0。`
  }
  if (summary.value.state === 'ready') {
    return `样本基数 ${base}，命中 ${matched} 人，明细 ${shown} 条；成员数与样本基数的差异来自筛选条件。`
  }
  return `样本基数 ${base}，命中 ${matched} 人，明细 ${shown} 条；当前结果不可作为正式成员明细使用。`
})

function formatNumber(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : String(value)
}

async function readOnce(force: boolean): Promise<{ retryable: boolean; reason: string }> {
  const response = await request(`${ENDPOINT}/${props.groupId}/members`, { method: force ? 'POST' : 'GET' })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail || `成员明细读取失败（${response.status}）`)
  }
  const payload = await response.json()
  staleSummary.value = null
  summary.value = payload as MemberSummary
  members.value = (payload.members ?? []) as Member[]
  expanded.value = false
  return {
    retryable: payload.state === 'failed' && Boolean(payload.retryable),
    reason: String(payload.state_label ?? '计算失败'),
  }
}

async function reload(force = false) {
  if (loading.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    let lastReason = ''
    // 空态/暂无数据是确定结果，不重试；失败或中断的结果自动重试一次并说明原因。
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const result = await readOnce(force)
        if (!result.retryable) return
        lastReason = `成员明细计算失败（第 ${attempt + 1} 次），已自动重试`
      } catch (error) {
        lastReason = error instanceof Error ? error.message : '成员明细读取失败'
        if (attempt === 1) {
          // 维持最近一次确认结果，不静默改成空表。
          staleSummary.value = members.value.length || summary.value.reason_code ? { ...summary.value } : null
          errorMessage.value = `${lastReason}；已重试仍未成功，可稍后手动重试。`
          return
        }
      }
    }
    staleSummary.value = members.value.length ? { ...summary.value } : null
    errorMessage.value = `${lastReason}；已保留最近一次完整成员集合，可稍后重试。`
  } finally {
    loading.value = false
  }
}

watch(() => props.groupId, () => { members.value = []; void reload(false) }, { immediate: true })

defineExpose({ reload })
</script>
