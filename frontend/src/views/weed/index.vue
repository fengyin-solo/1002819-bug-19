<template>
  <section class="page" data-module="weed">
    <header class="page-head">
      <div>
        <h2>杂草清除管理</h2>
        <p class="page-desc">围绕除草编号、除草区域、杂草种类、覆盖程度做登记、安排、清除与复核；状态只能一步步往前走。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记除草任务</button>
        <button class="btn" type="button" @click="autoSchedule">一键安排待办除草</button>
        <button class="btn" type="button" @click="showReviewQueue">待复核清单</button>
        <button class="btn" type="button" @click="exportRows">导出清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>除草编号</span>
        <input v-model="keyword" placeholder="按除草编号检索" />
      </label>
      <label class="filter-item">
        <span>除草状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ display(row, column) }}</td>
          <td class="row-actions">
            <template v-if="actionsFor(row.status).length">
              <button
                v-for="action in actionsFor(row.status)"
                :key="action"
                class="link"
                type="button"
                @click="openAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <button class="link" type="button" @click="openDetail(row)">查看详情</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无杂草清除数据，可先登记除草任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条杂草清除记录</span>
      <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
    </footer>

    <!-- 登记 / 结果录入弹窗 -->
    <div v-if="formModal.open" class="modal-mask" @click.self="closeModal">
      <div class="modal">
        <h3 class="modal-title">{{ formModal.title }}</h3>
        <div class="form-grid">
          <label v-for="field in formModal.fields" :key="field" class="form-item">
            <span>{{ field }}<em v-if="formModal.required.includes(field)">*</em></span>
            <input
              v-model="formModal.values[field]"
              :type="field === '作业面积' ? 'number' : field === '作业日期' ? 'date' : 'text'"
              :placeholder="`请输入${field}`"
            />
          </label>
        </div>
        <div class="modal-foot">
          <button class="btn ghost" type="button" @click="closeModal">取消</button>
          <button class="btn primary" type="button" @click="submitForm">{{ formModal.submitLabel }}</button>
        </div>
      </div>
    </div>

    <!-- 详情弹窗：直接取单条接口，和列表同一数据源 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal">
        <h3 class="modal-title">除草任务详情 · {{ detail['除草编号'] }}</h3>
        <table class="detail-table">
          <tbody>
            <tr v-for="column in columns" :key="column">
              <th>{{ column }}</th>
              <td>{{ display(detail, column) }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-foot">
          <button class="btn primary" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/weed'
const columns = ['除草编号', '除草区域', '杂草种类', '覆盖程度', '作业面积', '除草方式', '作业日期', '作业人员', '除草状态']
const statuses = ['待安排', '已安排', '清除中', '待复核', '需补除', '已清除']
const baseFields = ['除草编号', '除草区域', '杂草种类', '覆盖程度', '作业面积', '除草方式', '作业日期', '作业人员']
const resultFields = ['覆盖程度', '作业面积', '除草方式', '作业日期', '作业人员']
const resultRequired = ['覆盖程度', '作业面积']

// 每个状态只露出允许的下一步动作，状态不能后退、不能跳步
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  待安排: ['安排除草'],
  已安排: ['开始清除'],
  清除中: ['登记清除结果', '重新获取结果'],
  待复核: ['复核通过', '复核驳回'],
  需补除: ['补除登记'],
  已清除: [],
}
const RESULT_ACTIONS = ['登记清除结果', '重新获取结果', '补除登记']

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref<Record<string, number>>({})
const message = ref('')
const messageOk = ref(true)
const keyword = ref('')
const statusFilter = ref('')
const detail = ref<Row | null>(null)

interface FormModal {
  open: boolean
  title: string
  action: string
  entryId: number | null
  creating: boolean
  fields: string[]
  required: string[]
  submitLabel: string
  values: Record<string, string>
}

const formModal = reactive<FormModal>({
  open: false,
  title: '',
  action: '',
  entryId: null,
  creating: false,
  fields: [],
  required: [],
  submitLabel: '',
  values: {},
})

const statCards = computed(() => [
  { label: '待安排', value: stats.value['待安排'] ?? 0 },
  { label: '已安排', value: stats.value['已安排'] ?? 0 },
  { label: '清除中', value: stats.value['清除中'] ?? 0 },
  { label: '待复核', value: stats.value['待复核'] ?? 0 },
  { label: '需补除', value: stats.value['需补除'] ?? 0 },
  { label: '已清除', value: stats.value['已清除'] ?? 0 },
  { label: '完成面积（亩）', value: stats.value['完成面积'] ?? 0 },
])

function actionsFor(status: unknown): string[] {
  return ACTIONS_BY_STATUS[String(status)] ?? []
}

function display(row: Row, column: string): string {
  const value = row[column]
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function setMessage(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

async function postJson(path: string, body: Record<string, unknown>) {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const payload = await response.json().catch(() => null)
  return { ok: response.ok && payload?.ok !== false, payload }
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function showReviewQueue() {
  keyword.value = ''
  statusFilter.value = '待复核'
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function emptyValues(fields: string[], row?: Row): Record<string, string> {
  const values: Record<string, string> = {}
  for (const field of fields) {
    values[field] = row && row[field] !== null && row[field] !== undefined ? String(row[field]) : ''
  }
  return values
}

function openCreate() {
  Object.assign(formModal, {
    open: true,
    title: '登记除草任务',
    action: '',
    entryId: null,
    creating: true,
    fields: baseFields,
    required: ['除草编号', '除草区域', '杂草种类'],
    submitLabel: '提交登记',
    values: emptyValues(baseFields),
  })
}

function openAction(action: string, row: Row) {
  if (action === '安排除草' || action === '复核通过' || action === '复核驳回') {
    void executeAction(action, row.id as number, {})
    return
  }
  Object.assign(formModal, {
    open: true,
    title: `${action} · ${row['除草编号']}`,
    action,
    entryId: row.id as number,
    creating: false,
    fields: resultFields,
    required: RESULT_ACTIONS.includes(action) ? resultRequired : [],
    submitLabel: action,
    values: emptyValues(resultFields, row),
  })
}

async function submitForm() {
  const missing = formModal.required.filter((field) => !formModal.values[field]?.trim())
  if (missing.length) {
    setMessage(`请填写必填项：${missing.join('、')}`, false)
    return
  }
  const values: Record<string, string> = {}
  for (const field of formModal.fields) {
    const text = formModal.values[field]?.trim()
    if (text) {
      values[field] = text
    }
  }
  if (formModal.creating) {
    const { ok, payload } = await postJson(ENDPOINT, { values })
    if (!ok) {
      setMessage(payload?.message ?? '除草任务登记失败', false)
      return
    }
    setMessage(payload?.message ?? '除草任务已登记')
  } else if (formModal.entryId !== null) {
    const { ok, payload } = await executeAction(formModal.action, formModal.entryId, values, true)
    if (!ok) {
      // 状态未改动，弹窗保留，重新取一次结果即可接着提交
      setMessage(payload?.message ?? '清除结果未取得，可重新获取后再次提交', false)
      return
    }
  }
  closeModal()
  await reload()
}

async function executeAction(action: string, entryId: number, values: Record<string, string>, keepModal = false) {
  const result = await postJson(`${ENDPOINT}/${entryId}/actions`, { values: { action, ...values } })
  if (result.ok) {
    setMessage(result.payload?.message ?? `已${action}`)
    if (!keepModal) {
      await reload()
    }
  } else {
    setMessage(result.payload?.message ?? '杂草清除动作未生效，请稍后重试', false)
  }
  return result
}

async function autoSchedule() {
  const { ok, payload } = await postJson(`${ENDPOINT}/auto-schedule`, {})
  setMessage(payload?.message ?? '自动安排完成', ok)
  await reload()
}

async function openDetail(row: Row) {
  setMessage('')
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      setMessage('除草任务详情读取失败', false)
      return
    }
    detail.value = (await response.json()) as Row
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '除草任务详情读取失败', false)
  }
}

function closeModal() {
  formModal.open = false
}

async function reload() {
  const query = new URLSearchParams()
  if (keyword.value.trim()) {
    query.set('keyword', keyword.value.trim())
  }
  if (statusFilter.value) {
    query.set('status', statusFilter.value)
  }
  try {
    const [listRes, statsRes] = await Promise.all([
      request(`${ENDPOINT}?${query.toString()}`),
      request(`${ENDPOINT}/stats`),
    ])
    if (!listRes.ok) {
      throw new Error('除草任务列表读取失败')
    }
    const payload = await listRes.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (statsRes.ok) {
      stats.value = await statsRes.json()
    }
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '杂草清除列表读取失败', false)
  }
}

onMounted(reload)
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.ok-text { color: #15803d; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: 640px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.2);
}
.modal-title { margin: 0 0 14px; font-size: 16px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
.form-item span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.form-item em { color: #b42318; font-style: normal; margin-left: 2px; }
.form-item input, .form-item select { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.filter-item select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.detail-table { width: 100%; border-collapse: collapse; }
.detail-table th, .detail-table td { border: 1px solid var(--border); padding: 7px 10px; font-size: 13px; text-align: left; }
.detail-table th { width: 110px; background: #f8fafc; color: var(--muted); font-weight: normal; }
</style>
