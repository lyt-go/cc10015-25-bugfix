<template>
  <section class="page" data-module="sample_group_detail">
    <header class="page-head">
      <div>
        <h2>样本分群详情</h2>
        <p class="page-desc">与列表页读取同一份成员确认结果：成员键稳定、样本基数与明细条数逐项可核对。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/sample_group" data-testid="back-to-list">返回列表</RouterLink>
        <button class="btn" type="button" data-testid="reload-detail" @click="reload">刷新详情</button>
        <button class="btn primary" type="button" data-testid="audit-detail" @click="loadAudit">
          核对留档
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="detail-error">{{ errorMessage }}</p>

    <table v-if="entry" class="data-table" data-testid="detail-fields">
      <thead>
        <tr><th v-for="field in columns" :key="field">{{ field }}</th><th>状态</th></tr>
      </thead>
      <tbody>
        <tr>
          <td v-for="field in columns" :key="field">{{ entry[field] ?? '—' }}</td>
          <td data-testid="detail-status">{{ entry.status }}</td>
        </tr>
      </tbody>
    </table>

    <MemberPanel
      v-if="entry && validGroupId"
      ref="panel"
      :group-id="groupId"
      :initial="summary"
      data-testid="detail-member-panel"
    />

    <section v-if="audit" class="audit-panel" data-testid="audit-panel">
      <h4>V025 留档核对</h4>
      <p class="page-desc">{{ auditNote }}</p>
      <pre class="audit-json">{{ auditText }}</pre>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { fetchMemberAudit, fetchMemberSummary, type MemberSummary } from '@/api/sampleGroupMembers'
import { request } from '@/api/client'
import MemberPanel from './MemberPanel.vue'

const route = useRoute()
const columns = ["分群编号", "分群名称", "筛选条件", "样本基数", "分群日期", "更新方式", "有效天数", "负责人"]
const entry = ref<Record<string, string | number | null> | null>(null)
const summary = ref<MemberSummary | null>(null)
const audit = ref<Record<string, unknown> | null>(null)
const errorMessage = ref('')
const panel = ref<InstanceType<typeof MemberPanel> | null>(null)

const groupId = computed(() => Number(route.params.id))
const validGroupId = computed(() => Number.isInteger(groupId.value) && groupId.value > 0)
const auditText = computed(() => JSON.stringify(audit.value, null, 2))
const auditNote = computed(() => {
  const residue = (audit.value?.residue ?? {}) as Record<string, unknown>
  return residue.ok
    ? `核对通过：${residue.detail ?? '明细表与快照一致'}`
    : `核对未通过：${residue.detail ?? '存在残留，下一次读取会整体回滚后重算'}`
})

async function reload() {
  errorMessage.value = ''
  if (!validGroupId.value) {
    entry.value = null
    errorMessage.value = `分群编号「${route.params.id}」不合法，请从列表页进入详情。`
    return
  }
  try {
    const response = await request(`/api/sample_group/${groupId.value}`)
    if (!response.ok) throw new Error(`样本分群 ${groupId.value} 读取失败（${response.status}）`)
    const payload = await response.json()
    entry.value = payload
    summary.value = payload.member_summary ?? (await fetchMemberSummary(groupId.value))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '样本分群详情读取失败'
  }
}

async function loadAudit() {
  errorMessage.value = ''
  try {
    audit.value = await fetchMemberAudit(groupId.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '留档核对读取失败'
  }
}

onMounted(reload)
</script>
