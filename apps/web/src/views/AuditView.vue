<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchAuditEvents, type AuditCenterEvent } from '../lib/auditApi'

type LoadState = 'loading' | 'ready' | 'empty' | 'failed'
type Category = 'ai' | 'human' | 'risk' | 'policy' | 'system'
type Filter = 'all' | Category

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'ai', label: 'AI' },
  { key: 'human', label: 'Human' },
  { key: 'risk', label: 'Risk' },
  { key: 'policy', label: 'Policy' },
  { key: 'system', label: 'System' },
]

const loadState = ref<LoadState>('loading')
const events = ref<AuditCenterEvent[]>([])
const filter = ref<Filter>('all')
const selected = ref<AuditCenterEvent | null>(null)
const notice = ref<string | null>(null)

// 内部事件类型 → 业务语言。只做产品 projection，不改动底层 event type。
const EVENT_LABELS: Record<string, string> = {
  policy_retrieved: '已检索企业政策',
  get_order: '已加载订单上下文',
  check_inventory: '已核查库存',
  decision_produced: 'AI 已生成处理建议',
  risk_gate: '风险门禁评估',
  approval_request_created: '已请求人工审批',
  approval_approved: '人工已批准',
  approval_rejected: '人工已拒绝',
  create_replacement: '已执行换货动作',
  update_ticket: '已回写工单',
  agent_run_outcome: 'Agent 运行终态',
}

// 每个事件归入唯一一个语义类别，用于筛选与标签展示。
function categoryOf(event: AuditCenterEvent): Category {
  if (event.event_type === 'policy_retrieved') return 'policy'
  if (event.event_type === 'risk_gate' || event.event_type === 'approval_request_created') return 'risk'
  if (event.event_type === 'approval_approved' || event.event_type === 'approval_rejected') return 'human'
  if (event.event_type === 'agent_run_outcome') return 'system'
  return 'ai'
}

const CATEGORY_LABELS: Record<Category, string> = {
  ai: 'AI',
  human: 'Human',
  risk: 'Risk',
  policy: 'Policy',
  system: 'System',
}

// 触发者类型 → 业务化来源。human 对应当前唯一的真实人工角色：运营团队。
const ACTOR_LABELS: Record<string, string> = {
  agent: 'AI Agent',
  system: 'System',
  human: 'Operations Team',
}

const OUTCOME_LABELS: Record<string, string> = {
  success: '成功',
  created: '已创建',
  updated: '已更新',
  completed: '已完成',
  failed: '失败',
  allow: '已放行',
  approval_required: '需人工审批',
  approved: '已批准',
  rejected: '已拒绝',
  eligible: '符合换货条件',
  ineligible: '不符合换货条件',
  sku_not_found: 'SKU 未找到',
  unavailable: '不可用',
  duplicate: '重复',
  no_relevant_policy: '无相关政策',
  corpus_unavailable: '政策语料不可用',
}

function eventLabel(event: AuditCenterEvent): string {
  return EVENT_LABELS[event.event_type] ?? event.action
}

function categoryLabel(event: AuditCenterEvent): string {
  return CATEGORY_LABELS[categoryOf(event)]
}

function actorLabel(event: AuditCenterEvent): string {
  return ACTOR_LABELS[event.actor_type] ?? event.actor_type
}

function outcomeLabel(event: AuditCenterEvent): string {
  return OUTCOME_LABELS[event.outcome] ?? event.outcome
}

function metaText(event: AuditCenterEvent, key: string): string | null {
  const value = event.metadata?.[key]
  if (typeof value !== 'string' && typeof value !== 'number') return null
  return String(value)
}

function reasonCode(event: AuditCenterEvent): string | null {
  return metaText(event, 'reason_code') ?? metaText(event, 'rule_code')
}

function parseDate(value: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`)
}

function formatTime(value: string): string {
  const date = parseDate(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date)
}

function formatDateTime(value: string): string {
  const date = parseDate(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date)
}

const filteredEvents = computed(() => {
  if (filter.value === 'all') return events.value
  return events.value.filter(event => categoryOf(event) === filter.value)
})

function matchesFilter(filterKey: Filter): boolean {
  return filter.value === filterKey
}

function countFor(filterKey: Filter): number {
  if (filterKey === 'all') return events.value.length
  return events.value.filter(event => categoryOf(event) === filterKey).length
}

async function loadEvents(): Promise<void> {
  loadState.value = 'loading'
  notice.value = null
  selected.value = null
  try {
    events.value = await fetchAuditEvents()
    loadState.value = events.value.length ? 'ready' : 'empty'
  } catch {
    events.value = []
    loadState.value = 'failed'
    notice.value = '审计服务暂时不可用。未展示任何伪造事件，请恢复服务后重试。'
  }
}

onMounted(loadEvents)
</script>

<template>
  <div class="audit-workspace">
    <section class="page-heading">
      <div>
        <div class="eyebrow">AI GOVERNANCE · AUDIT TRAIL</div>
        <h1>Audit <span>审计中心</span></h1>
        <p>追踪 AI 服务流程中的关键业务事件、风险决策与人工干预。</p>
      </div>
      <div class="heading-actions">
        <button class="refresh-button" type="button" :disabled="loadState === 'loading'" @click="loadEvents">↻ 刷新</button>
      </div>
    </section>

    <div v-if="notice" class="notice" role="status">
      <span>{{ notice }}</span>
      <button v-if="loadState === 'failed'" type="button" @click="loadEvents">重试</button>
    </div>

    <section class="filter-bar" aria-label="审计事件筛选">
      <button
        v-for="item in FILTERS"
        :key="item.key"
        type="button"
        :class="{ active: matchesFilter(item.key) }"
        @click="filter = item.key"
      >
        {{ item.label }} <b>{{ countFor(item.key) }}</b>
      </button>
    </section>

    <section class="audit-panel">
      <header class="panel-heading">
        <div><h2>审计事件</h2><p>来自持久化 AuditEvent · 按发生时间倒序</p></div>
        <span>{{ filteredEvents.length }} events</span>
      </header>

      <div v-if="loadState === 'loading'" class="state-view" data-state="loading">
        <span class="spinner"></span><h3>正在加载审计中心</h3><p>正在同步真实 AuditEvent 与关联上下文…</p>
      </div>

      <div v-else-if="loadState === 'failed'" class="state-view" data-state="failed">
        <span class="empty-icon error">!</span><h3>审计数据暂时不可用</h3><p>页面不会用示例记录替代真实 AuditEvent。</p>
        <button class="primary-button" type="button" @click="loadEvents">重试连接</button>
      </div>

      <div v-else-if="loadState === 'empty'" class="state-view" data-state="empty">
        <span class="empty-icon">✓</span><h3>暂无审计事件</h3><p>真实执行产生审计事件后会自动出现在这里。</p>
      </div>

      <div v-else-if="filteredEvents.length === 0" class="state-view" data-state="empty">
        <span class="empty-icon">✓</span><h3>该筛选下暂无事件</h3><p>当前类别还没有真实审计事件。</p>
      </div>

      <div v-else class="audit-table" data-testid="audit-table">
        <div class="table-header">
          <span>Time</span><span>Event</span><span>Ticket</span><span>Agent Run</span><span>Actor / Source</span><span>Result</span>
        </div>
        <article
          v-for="event in filteredEvents"
          :key="event.event_key"
          class="audit-row"
          :class="{ active: selected?.event_key === event.event_key }"
          @click="selected = event"
        >
          <time>{{ formatTime(event.occurred_at) }}</time>
          <div class="event-cell">
            <strong>{{ eventLabel(event) }}</strong>
            <small>{{ event.summary }}</small>
            <span class="category-tag" :data-category="categoryOf(event)">{{ categoryLabel(event) }}</span>
          </div>
          <a class="ref-link" :href="`#/tickets/${event.ticket_key}`" @click.stop>{{ event.ticket_key }}</a>
          <a class="ref-link" :href="`#/agent-runs/${event.agent_run_key}`" @click.stop>{{ event.agent_run_key }}</a>
          <span class="actor-tag" :data-actor="event.actor_type">{{ actorLabel(event) }}</span>
          <span class="result" :data-success="event.success"><i></i>{{ outcomeLabel(event) }}</span>
        </article>
      </div>
    </section>

    <div v-if="selected" class="drawer-layer" @click.self="selected = null">
      <aside class="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="audit-detail-title">
        <header class="drawer-header">
          <div>
            <span class="detail-kicker">AUDIT EVENT</span>
            <h2 id="audit-detail-title">{{ eventLabel(selected) }}</h2>
            <p>{{ selected.ticket_key }} · {{ selected.agent_run_key }}</p>
            <nav class="drawer-nav">
              <a :href="`#/tickets/${selected.ticket_key}`">查看工单</a>
              <a :href="`#/agent-runs/${selected.agent_run_key}`">查看 Agent Run</a>
              <a v-if="selected.approval_key" :href="`#/approvals?approval=${selected.approval_key}`">查看审批</a>
            </nav>
          </div>
          <button class="close-button" type="button" aria-label="关闭详情" @click="selected = null">×</button>
        </header>

        <div class="drawer-scroll">
          <section class="detail-section">
            <div class="section-title"><span>01</span><div><h3>业务摘要</h3><p>以业务语言描述该事件</p></div><span class="outcome-pill" :data-success="selected.success">{{ outcomeLabel(selected) }}</span></div>
            <p class="summary-copy">{{ selected.summary }}</p>
            <div class="context-grid">
              <div><small>类型</small><strong>{{ categoryLabel(selected) }}</strong><span>{{ eventLabel(selected) }}</span></div>
              <div><small>触发者</small><strong>{{ actorLabel(selected) }}</strong><span>{{ selected.actor_type }}</span></div>
              <div><small>发生时间</small><strong>{{ formatDateTime(selected.occurred_at) }}</strong><span>{{ selected.occurred_at }}</span></div>
            </div>
          </section>

          <section class="detail-section">
            <div class="section-title"><span>02</span><div><h3>关联上下文</h3><p>复用已有业务标识</p></div></div>
            <dl class="link-grid">
              <div><dt>Ticket</dt><dd><a :href="`#/tickets/${selected.ticket_key}`">{{ selected.ticket_key }}</a></dd></div>
              <div><dt>Agent Run</dt><dd><a :href="`#/agent-runs/${selected.agent_run_key}`">{{ selected.agent_run_key }}</a></dd></div>
              <div><dt>Approval</dt><dd><a v-if="selected.approval_key" :href="`#/approvals?approval=${selected.approval_key}`">{{ selected.approval_key }}</a><span v-else class="muted">—</span></dd></div>
              <div><dt>业务对象</dt><dd><span class="muted">{{ selected.affected_object_type ?? '—' }}</span> {{ selected.affected_object_key ?? '' }}</dd></div>
            </dl>
          </section>

          <section v-if="categoryOf(selected) === 'risk'" class="detail-section risk-context">
            <div class="section-title"><span>03</span><div><h3>风险依据</h3><p>风险事件的结构化快照</p></div></div>
            <dl>
              <div><dt>风险等级</dt><dd>{{ metaText(selected, 'level') ?? '—' }}</dd></div>
              <div><dt>规则码</dt><dd>{{ metaText(selected, 'rule_code') ?? '—' }}</dd></div>
              <div><dt>受保护动作</dt><dd>{{ metaText(selected, 'protected_action') ?? '—' }}</dd></div>
            </dl>
          </section>

          <section v-else-if="categoryOf(selected) === 'policy'" class="detail-section">
            <div class="section-title"><span>03</span><div><h3>政策依据</h3><p>已检索的企业政策</p></div></div>
            <dl>
              <div><dt>检索状态</dt><dd>{{ metaText(selected, 'status') ?? selected.outcome }}</dd></div>
            </dl>
          </section>

          <section class="detail-section">
            <details class="developer-details">
              <summary>Developer Details <span>event type · internal ids · reason_code · raw metadata</span></summary>
              <dl>
                <div><dt>event type</dt><dd><code>{{ selected.event_type }}</code></dd></div>
                <div><dt>event key</dt><dd><code>{{ selected.event_key }}</code></dd></div>
                <div><dt>ticket key</dt><dd><code>{{ selected.ticket_key }}</code></dd></div>
                <div><dt>agent run key</dt><dd><code>{{ selected.agent_run_key }}</code></dd></div>
                <div><dt>approval key</dt><dd><code>{{ selected.approval_key ?? '—' }}</code></dd></div>
                <div><dt>affected object</dt><dd><code>{{ selected.affected_object_type ?? '—' }} / {{ selected.affected_object_key ?? '—' }}</code></dd></div>
                <div><dt>reference</dt><dd><code>{{ selected.reference_type ?? '—' }} / {{ selected.reference_key ?? '—' }}</code></dd></div>
                <div v-if="reasonCode(selected)"><dt>reason_code</dt><dd><code>{{ reasonCode(selected) }}</code></dd></div>
                <div><dt>raw metadata</dt><dd><pre>{{ selected.metadata ? JSON.stringify(selected.metadata, null, 2) : '—' }}</pre></dd></div>
              </dl>
            </details>
          </section>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
* { box-sizing: border-box; }
.audit-workspace { max-width: 1600px; margin: 0 auto; padding: 38px 42px 56px; color: #172033; }
.page-heading { display: flex; justify-content: space-between; align-items: flex-end; gap: 30px; margin-bottom: 22px; }
.eyebrow,.detail-kicker { color: #6d5bd0; font-size: 11px; font-weight: 800; letter-spacing: .13em; }
.page-heading h1 { margin: 8px 0 8px; font-size: clamp(28px, 2.4vw, 38px); line-height: 1.15; letter-spacing: -.035em; }
.page-heading h1 span { margin-left: 10px; color: #737b8c; font-size: .5em; font-weight: 500; letter-spacing: 0; }
.page-heading p { margin: 0; color: #687184; font-size: 14px; }
.heading-actions { display: flex; align-items: center; gap: 12px; }
.refresh-button { padding: 9px 12px; border: 1px solid #dce1e9; border-radius: 8px; background: #fff; color: #556074; font: inherit; font-size: 12px; cursor: pointer; }
.notice { display: flex; justify-content: space-between; align-items: center; margin: -8px 0 18px; padding: 11px 14px; border: 1px solid #cbdcf4; border-radius: 9px; background: #f5f9ff; color: #31567f; font-size: 12px; }
.notice button { border: 0; background: transparent; color: inherit; font-weight: 700; cursor: pointer; }
.filter-bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }
.filter-bar button { display: inline-flex; align-items: center; gap: 7px; padding: 7px 12px; border: 1px solid #e0e4ea; border-radius: 8px; background: #fff; color: #556074; font: inherit; font-size: 11px; font-weight: 700; cursor: pointer; }
.filter-bar button b { min-width: 18px; padding: 1px 5px; border-radius: 9px; background: #f1f2f5; color: #868e9c; font-size: 9px; text-align: center; }
.filter-bar button.active { border-color: #6557c8; background: #eeecfb; color: #5c4eb6; }
.filter-bar button.active b { background: #fff; color: #6557c8; }
.audit-panel { overflow: hidden; border: 1px solid #e1e5ec; border-radius: 12px; background: #fff; box-shadow: 0 5px 18px rgba(31,42,68,.045); }
.panel-heading { display: flex; justify-content: space-between; align-items: center; padding: 21px 24px; border-bottom: 1px solid #e8ebf0; }
.panel-heading h2 { margin: 0; font-size: 15px; }.panel-heading p { margin: 4px 0 0; color: #8a92a1; font-size: 11px; }.panel-heading>span { padding: 5px 9px; border-radius: 6px; background: #f3f4f7; color: #777f8d; font-size: 10px; font-weight: 700; text-transform: uppercase; }
.table-header,.audit-row { display: grid; grid-template-columns: 90px minmax(220px,1.25fr) minmax(150px,.9fr) minmax(150px,.9fr) 130px 120px; column-gap: 18px; align-items: center; }
.table-header { padding: 10px 24px; background: #f8f9fb; color: #8a92a1; font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
.audit-row { min-height: 78px; padding: 13px 24px; border-top: 1px solid #edf0f4; transition: background .15s; cursor: pointer; }
.audit-row:first-of-type { border-top: 0; }.audit-row:hover,.audit-row.active { background: #fafbff; }
.audit-row time { color: #6f7786; font-size: 11px; font-variant-numeric: tabular-nums; }
.event-cell { min-width: 0; }.event-cell strong { display: block; overflow: hidden; color: #252d3d; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.event-cell small { display: -webkit-box; overflow: hidden; margin-top: 4px; color: #858d9c; font-size: 10.5px; line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 1; }
.category-tag { display: inline-block; margin-top: 6px; padding: 2px 7px; border-radius: 5px; background: #eef3ff; color: #426aca; font-size: 9px; font-weight: 800; letter-spacing: .04em; text-transform: uppercase; }
.category-tag[data-category="human"] { background: #edfaf4; color: #14855d; }.category-tag[data-category="risk"] { background: #fff0f0; color: #d34040; }.category-tag[data-category="policy"] { background: #eef3ff; color: #426aca; }.category-tag[data-category="system"] { background: #f2f3f6; color: #6a7280; }
.ref-link { overflow: hidden; color: #6354bd; font-size: 11px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; text-decoration: none; }.ref-link:hover { text-decoration: underline; }
.actor-tag { display: inline-block; width: fit-content; padding: 4px 8px; border-radius: 6px; background: #f3f4f7; color: #5e6878; font-size: 10px; font-weight: 700; }.actor-tag[data-actor="human"] { background: #edfaf4; color: #14855d; }.actor-tag[data-actor="agent"] { background: #eeecfb; color: #5c4eb6; }
.result { display: inline-flex; width: fit-content; align-items: center; gap: 6px; color: #434c5c; font-size: 11px; font-weight: 700; }.result i { width: 6px; height: 6px; border-radius: 50%; background: #23a172; }.result[data-success="false"] i { background: #d98932; }
.state-view { display: grid; min-height: 300px; place-items: center; align-content: center; padding: 50px; text-align: center; }.state-view h3 { margin: 14px 0 5px; font-size: 16px; }.state-view p { margin: 0 0 18px; color: #858d9c; font-size: 12px; }.empty-icon { display: grid; place-items: center; width: 46px; height: 46px; border-radius: 50%; background: #edf9f4; color: #13835e; font-size: 20px; }.empty-icon.error { background: #fff0f0; color: #b43d3d; }
.spinner { display: inline-block; width: 20px; height: 20px; border: 2px solid #dedbea; border-top-color: #6758c5; border-radius: 50%; animation: spin .8s linear infinite; }@keyframes spin { to { transform: rotate(360deg); } }
.primary-button { border: 0; border-radius: 8px; background: #6557c8; color: #fff; font: inherit; font-size: 12px; font-weight: 700; cursor: pointer; padding: 10px 15px; }
.drawer-layer { position: fixed; z-index: 50; inset: 0; display: flex; justify-content: flex-end; background: rgba(22,29,44,.32); backdrop-filter: blur(2px); }
.detail-drawer { display: flex; width: min(720px, 92vw); height: 100vh; flex-direction: column; background: #f8f9fb; box-shadow: -18px 0 50px rgba(22,29,44,.16); }
.drawer-header { display: flex; justify-content: space-between; padding: 25px 30px 20px; border-bottom: 1px solid #e1e5eb; background: #fff; }.drawer-header h2 { margin: 6px 0 3px; font-size: 21px; letter-spacing: -.02em; }.drawer-header p { margin: 0; color: #8a92a0; font-size: 10px; }.drawer-nav { display: flex; gap: 8px; margin-top: 10px; padding: 0; }.drawer-nav a { height: auto; margin: 0; padding: 5px 8px; border: 1px solid #ddd9f3; border-radius: 6px; color: #6253b7; font-size: 8.5px; text-decoration: none; }.close-button { align-self: flex-start; width: 30px; height: 30px; border-radius: 8px; background: #f2f3f6; color: #5e6878; font-size: 20px; border: 0; cursor: pointer; }
.drawer-scroll { overflow-y: auto; flex: 1; padding: 22px 28px 40px; }.detail-section { margin-bottom: 18px; padding: 20px; border: 1px solid #e1e5eb; border-radius: 11px; background: #fff; }.section-title { display: flex; align-items: center; gap: 10px; margin-bottom: 17px; }.section-title>span:first-child { display: grid; place-items: center; width: 25px; height: 25px; border-radius: 7px; background: #f0eefb; color: #6858c2; font-size: 9px; font-weight: 800; }.section-title h3 { margin: 0; font-size: 13px; }.section-title p { margin: 2px 0 0; color: #9299a6; font-size: 9.5px; }.outcome-pill { margin-left: auto; padding: 4px 7px; border-radius: 5px; background: #eef8f4; color: #17805b; font-size: 9px; font-weight: 700; }.outcome-pill[data-success="false"] { background: #fff3e8; color: #b96d18; }
.summary-copy { margin: 0 0 16px; color: #50596a; font-size: 12px; line-height: 1.65; }
.context-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; }.context-grid>div { padding: 12px; border-radius: 8px; background: #f7f8fa; }.context-grid small,.link-grid dt,.risk-context dt { color: #949ba8; font-size: 9px; font-weight: 700; text-transform: uppercase; }.context-grid strong { display: block; overflow: hidden; margin: 5px 0 3px; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }.context-grid span { display: block; overflow: hidden; color: #7f8796; font-size: 9.5px; text-overflow: ellipsis; white-space: nowrap; }
.link-grid { margin: 0; }.link-grid div,.risk-context dl div { display: grid; grid-template-columns: 130px 1fr; padding: 8px 0; border-bottom: 1px solid #eef0f3; }.link-grid div:last-child,.risk-context dl div:last-child { border-bottom: 0; }.link-grid dd,.risk-context dd { margin: 0; color: #4e5767; font-size: 10.5px; overflow-wrap: anywhere; }.link-grid a { color: #6354bd; text-decoration: none; }.link-grid a:hover { text-decoration: underline; }.muted { color: #9aa1ad; }
.risk-context dl { margin: 0; }
.developer-details { border: 1px solid #e5e7eb; border-radius: 9px; }.developer-details summary { padding: 12px 14px; color: #556074; font-size: 11px; font-weight: 700; cursor: pointer; list-style: none; }.developer-details summary span { margin-left: 8px; color: #9aa1ad; font-size: 9px; font-weight: 500; }.developer-details dl { margin: 0; padding: 0 14px 12px; }.developer-details dl div { display: grid; grid-template-columns: 150px 1fr; padding: 7px 0; border-bottom: 1px solid #f0f2f5; }.developer-details dl div:last-child { border-bottom: 0; }.developer-details dt { color: #949ba8; font-size: 9px; font-weight: 700; text-transform: uppercase; }.developer-details dd { margin: 0; color: #4e5767; font-size: 10px; overflow-wrap: anywhere; }.developer-details code { padding: 2px 5px; border-radius: 4px; background: #f2f3f5; font-size: 9.5px; }.developer-details pre { margin: 0; padding: 8px; border-radius: 6px; background: #f6f7f9; color: #4e5767; font-size: 9.5px; overflow-x: auto; white-space: pre-wrap; }
button:disabled { cursor: wait; opacity: .6; }
@media (max-width: 1200px) { .audit-workspace { padding: 30px 26px; }.table-header,.audit-row { grid-template-columns: 80px minmax(180px,1.2fr) minmax(130px,.9fr) minmax(130px,.9fr) 110px 100px; column-gap: 12px; }.audit-row { padding-left: 18px; padding-right: 18px; }.table-header { padding-left: 18px; padding-right: 18px; } }
@media (max-width: 900px) { .table-header { display: none; }.audit-row { grid-template-columns: 1fr auto; gap: 10px; }.audit-row>*:not(.event-cell) { grid-column: 1/2; }.event-cell { grid-column: 1/2; }.context-grid { grid-template-columns: 1fr; }.page-heading { align-items: flex-start; flex-direction: column; } }
</style>
