<template>
  <section class="page" data-module="weed">
    <header class="page-head">
      <div>
        <h2>杂草清除管理</h2>
        <p class="page-desc">登记除草任务、跟踪清除与补除结果；清除状态只能逐步推进，已清完的结果进入待复核清单。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记除草任务</button>
        <button class="btn" type="button" @click="exportRows">导出杂草清除清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="tab-bar">
      <button class="tab-item" :class="{ active: tab === 'tasks' }" type="button" @click="switchTab('tasks')">
        作业清单
      </button>
      <button class="tab-item" :class="{ active: tab === 'review' }" type="button" @click="switchTab('review')">
        待复核清单<span class="tab-badge">{{ stats.待复核 }}</span>
      </button>
    </div>

    <form v-if="tab === 'tasks'" class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>除草编号</span>
        <input v-model="filters.keyword" placeholder="按除草编号检索" />
      </label>
      <label class="filter-item">
        <span>清除状态</span>
        <select v-model="filters.status">
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
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in pagedRows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ displayValue(row, column) }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">明细</button>
            <template v-if="tab === 'tasks'">
              <button
                v-for="action in nextActions(String(row.status))"
                :key="action"
                class="link"
                type="button"
                @click="openAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <template v-else>
              <button class="link" type="button" @click="review(row, true)">复核通过</button>
              <button class="link" type="button" @click="review(row, false)">转补除</button>
            </template>
          </td>
        </tr>
        <tr v-if="!pagedRows.length">
          <td :colspan="columns.length + 1" class="empty-state">
            {{ tab === 'review' ? '待复核清单为空，清除结果复核完成后会自动出列' : '暂无杂草清除数据，可先登记除草任务' }}
          </td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ tab === 'review' ? '待复核' : '杂草清除' }}记录</span>
      <span v-if="message.text" :class="message.ok ? 'ok-text' : 'error-text'">{{ message.text }}</span>
    </footer>

    <!-- 登记除草任务 -->
    <div v-if="dialog === 'create'" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <div class="modal-head">
          <h3>登记除草任务</h3>
          <button class="modal-close" type="button" @click="closeDialog">×</button>
        </div>
        <form @submit.prevent="submitCreate">
          <div class="form-grid">
            <label>除草编号 *<input v-model="form.除草编号" placeholder="如 WEED-0005" /></label>
            <label>除草区域 *<input v-model="form.除草区域" placeholder="同区域只挂一个未结案任务" /></label>
            <label class="span-2">杂草种类 *<input v-model="form.杂草种类" placeholder="如 狗尾草、香附子" /></label>
            <label>初始覆盖程度<input v-model="form.覆盖程度" placeholder="如 约40%" /></label>
            <label>作业面积（亩）<input v-model="form.作业面积" placeholder="如 3.5" /></label>
            <label>作业日期<input v-model="form.作业日期" type="date" @change="onDateChange" /></label>
            <label>除草方式<input v-model="form.除草方式" placeholder="留空按日期季节联动" @input="methodTouched = true" /></label>
            <label class="span-2">作业人员<input v-model="form.作业人员" /></label>
          </div>
          <p v-if="dialogError" class="form-error">{{ dialogError }}</p>
          <div class="modal-foot">
            <button class="btn" type="button" @click="closeDialog">取消</button>
            <button class="btn primary" type="submit">提交登记</button>
          </div>
        </form>
      </div>
    </div>

    <!-- 状态动作：清除结果随动作一起登记，失败可直接重试 -->
    <div v-else-if="dialog === 'action'" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <div class="modal-head">
          <h3>{{ activeAction }} · {{ activeRow?.['除草编号'] }}</h3>
          <button class="modal-close" type="button" @click="closeDialog">×</button>
        </div>
        <form @submit.prevent="submitAction">
          <div class="form-grid">
            <template v-if="activeAction === '安排除草'">
              <label>作业日期<input v-model="form.作业日期" type="date" @change="onDateChange" /></label>
              <label>除草方式<input v-model="form.除草方式" placeholder="留空按日期季节联动" @input="methodTouched = true" /></label>
              <label class="span-2">作业人员<input v-model="form.作业人员" /></label>
            </template>
            <template v-else>
              <label>{{ activeAction === '补除完成' ? '补除后覆盖程度' : '清除后覆盖程度' }}
                <input v-model="form.覆盖程度" placeholder="如 已降至2%" />
              </label>
              <label>作业面积（亩）<input v-model="form.作业面积" placeholder="完成面积随明细重算" /></label>
              <label>作业日期<input v-model="form.作业日期" type="date" @change="onDateChange" /></label>
              <label>除草方式<input v-model="form.除草方式" placeholder="留空按日期季节联动" @input="methodTouched = true" /></label>
              <label class="span-2">作业人员<input v-model="form.作业人员" /></label>
            </template>
          </div>
          <p class="page-desc" style="margin-top:8px">
            {{ actionHint }}
          </p>
          <p v-if="dialogError" class="form-error">{{ dialogError }}</p>
          <div class="modal-foot">
            <button class="btn" type="button" @click="closeDialog">取消</button>
            <button class="btn primary" type="submit">确认{{ activeAction }}</button>
          </div>
        </form>
      </div>
    </div>

    <!-- 明细：与清单同一数据源 -->
    <div v-else-if="dialog === 'detail'" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <div class="modal-head">
          <h3>除草任务明细 · {{ activeRow?.['除草编号'] }}</h3>
          <button class="modal-close" type="button" @click="closeDialog">×</button>
        </div>
        <dl v-if="activeRow" class="detail-list">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ displayValue(activeRow, field) }}</dd>
          </template>
          <dt>清除状态</dt>
          <dd>{{ activeRow.status }}<span v-if="activeRow.pending_review" class="tab-badge">待复核</span></dd>
        </dl>
        <div class="modal-foot">
          <button class="btn" type="button" @click="closeDialog">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type Stats = Record<string, number>

const ENDPOINT = '/api/weed'
const columns = ['除草编号', '除草区域', '杂草种类', '覆盖程度', '除草方式', '作业日期', '作业人员', '作业面积', '除草状态']
const detailFields = ['除草编号', '除草区域', '杂草种类', '覆盖程度', '作业面积', '除草方式', '作业日期', '作业人员']
const statuses = ['待清除', '清除中', '已清除', '需补除']
const STATUS_NEXT: Record<string, string[]> = {
  待清除: ['安排除草'],
  清除中: ['开始清除'],
  已清除: ['补除登记'],
  需补除: ['补除完成'],
}

const rows = ref<Row[]>([])
const reviewRows = ref<Row[]>([])
const total = ref(0)
const tab = ref<'tasks' | 'review'>('tasks')
const filters = reactive<{ keyword: string; status: string }>({ keyword: '', status: '' })
const message = reactive<{ text: string; ok: boolean }>({ text: '', ok: true })

const stats = ref<Stats>({ 待清除: 0, 清除中: 0, 已清除: 0, 需补除: 0, 待复核: 0, 完成面积: 0 })
const statCards = computed(() => [
  { label: '待清除区域', value: stats.value['待清除'] },
  { label: '清除中区域', value: stats.value['清除中'] },
  { label: '需补除区域', value: stats.value['需补除'] },
  { label: '待复核', value: stats.value['待复核'] },
  { label: '完成面积（亩）', value: stats.value['完成面积'] },
])

const pagedRows = computed(() => (tab.value === 'review' ? reviewRows.value : rows.value))

// 弹窗状态
const dialog = ref<'' | 'create' | 'action' | 'detail'>('')
const activeAction = ref('')
const activeRow = ref<Row | null>(null)
const dialogError = ref('')
// 用户是否手动改过除草方式：没改过就随作业日期按季节联动，改过后以人工填报为准。
const methodTouched = ref(false)
const emptyForm = (): Record<string, string> => ({
  除草编号: '', 除草区域: '', 杂草种类: '', 覆盖程度: '', 作业面积: '', 作业日期: '', 除草方式: '', 作业人员: '',
})
const form = reactive<Record<string, string>>(emptyForm())

function deriveMethod(workDate: string): string {
  const month = Number((workDate || '').slice(5, 7))
  if (!month) return ''
  if (month >= 3 && month <= 5) return '人工除草'
  if (month >= 6 && month <= 8) return '化学除草'
  if (month >= 9 && month <= 11) return '机械除草'
  return '覆盖抑草'
}

function onDateChange() {
  if (methodTouched.value) return
  const method = deriveMethod(form['作业日期'])
  if (method) form['除草方式'] = method
}

const actionHint = computed(() => {
  if (activeAction.value === '安排除草') return '同一片区域重复安排只生效一次；作业日期变动后，除草方式按季节联动。'
  if (activeAction.value === '开始清除') return '清除结果登记后进入待复核清单；状态只能单步推进，无法退回待清除。'
  if (activeAction.value === '补除登记') return '任务转入需补除，由班组补做后登记补除结果。'
  return '补除结果登记后重新进入待复核清单；请求没取到结果时可直接再次提交，不会重复生效。'
})

function displayValue(row: Row, column: string): string | number {
  if (column === '除草状态') return String(row.status ?? '—')
  const value = row[column]
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function nextActions(status: string): string[] {
  return STATUS_NEXT[status] ?? []
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  void reload()
}

function switchTab(next: 'tasks' | 'review') {
  tab.value = next
  message.text = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function closeDialog() {
  dialog.value = ''
  activeAction.value = ''
  activeRow.value = null
  dialogError.value = ''
  methodTouched.value = false
  Object.assign(form, emptyForm())
}

function openCreate() {
  dialogError.value = ''
  methodTouched.value = false
  Object.assign(form, emptyForm())
  dialog.value = 'create'
}

function openDetail(row: Row) {
  activeRow.value = row
  dialog.value = 'detail'
}

function openAction(action: string, row: Row) {
  activeAction.value = action
  activeRow.value = row
  dialogError.value = ''
  methodTouched.value = false
  // 带出已登记内容，接口没取到结果时可以原样再交一次。
  Object.assign(form, emptyForm(), {
    覆盖程度: str(row['覆盖程度']),
    作业面积: str(row['作业面积']),
    作业日期: str(row['作业日期']),
    除草方式: str(row['除草方式']),
    作业人员: str(row['作业人员']),
  })
  dialog.value = 'action'
}

function str(value: unknown): string {
  return value === null || value === undefined ? '' : String(value)
}

function formPayload(extra?: Record<string, unknown>): Record<string, unknown> {
  const values: Record<string, unknown> = { ...extra }
  for (const [key, value] of Object.entries(form)) {
    if (value.trim()) values[key] = value.trim()
  }
  return values
}

async function submitCreate() {
  dialogError.value = ''
  try {
    const response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: formPayload() }) })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      dialogError.value = payload.message || '除草任务登记失败，可修改后重试'
      return
    }
    closeDialog()
    await refreshAll()
    flash(`除草任务 ${payload.entry['除草编号']} 已登记`, true)
  } catch (error) {
    dialogError.value = error instanceof Error ? error.message : '除草任务登记失败，可再次提交重试'
  }
}

async function submitAction() {
  if (!activeRow.value) return
  dialogError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${activeRow.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: formPayload({ action: activeAction.value }) }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      // 保留表单内容，允许取上次结果失败后直接重试。
      dialogError.value = payload.message || '动作未生效，可再次提交重试'
      return
    }
    closeDialog()
    await refreshAll()
    flash(payload.message, true)
  } catch (error) {
    dialogError.value = error instanceof Error ? error.message : '请求未送达，结果未登记，请重试'
  }
}

async function review(row: Row, passed: boolean) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/review`, {
      method: 'POST',
      body: JSON.stringify({ values: { 结论: passed ? '通过' : '不通过' } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      flash(payload.message || '复核未生效，请重试', false)
      return
    }
    await refreshAll()
    flash(payload.message, true)
  } catch (error) {
    flash(error instanceof Error ? error.message : '复核请求失败，请重试', false)
  }
}

function flash(text: string, ok: boolean) {
  message.text = text
  message.ok = ok
}

async function reload() {
  try {
    if (tab.value === 'review') {
      const payload = await fetchJsonSafe(`${ENDPOINT}/review`)
      reviewRows.value = payload.items ?? []
      total.value = payload.total ?? reviewRows.value.length
    } else {
      const params = new URLSearchParams()
      if (filters.keyword.trim()) params.set('keyword', filters.keyword.trim())
      if (filters.status) params.set('status', filters.status)
      const payload = await fetchJsonSafe(`${ENDPOINT}?${params.toString()}`)
      rows.value = payload.items ?? []
      total.value = payload.total ?? rows.value.length
    }
  } catch (error) {
    flash(error instanceof Error ? error.message : '杂草清除列表读取失败', false)
  }
}

async function reloadStats() {
  try {
    const payload = await fetchJsonSafe(`${ENDPOINT}/stats`)
    stats.value = payload
  } catch {
    // 看板暂时取不到不阻断清单操作，下次刷新自动重取。
  }
}

async function fetchJsonSafe(path: string) {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}，数据未更新`)
  return response.json()
}

async function refreshAll() {
  await Promise.all([reload(), reloadStats()])
}

onMounted(refreshAll)
</script>
