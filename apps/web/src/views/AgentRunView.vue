<script setup lang="ts">
defineProps<{ ticketKey: string }>()

/**
 * Agent Run 步骤状态模型。当前仅用于前端结构预览，
 * 与后续 T010 AgentRun 持久化 / T018 实际执行能力对接，
 * 现在不产生任何真实执行数据。
 */
type AgentStepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

const STEP_STATUS_LABELS: Record<AgentStepStatus, string> = {
  pending: '待执行',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过',
}

/** 状态图例顺序固定，避免遍历对象导致顺序不确定 */
const stepStatusLegend: { status: AgentStepStatus; label: string }[] = [
  { status: 'pending', label: STEP_STATUS_LABELS.pending },
  { status: 'running', label: STEP_STATUS_LABELS.running },
  { status: 'completed', label: STEP_STATUS_LABELS.completed },
  { status: 'failed', label: STEP_STATUS_LABELS.failed },
  { status: 'skipped', label: STEP_STATUS_LABELS.skipped },
]

interface StepPreview {
  name: string
  status: AgentStepStatus
}

/**
 * 结构预览的示意步骤 —— 仅用于展示时间线 item 能表达 step name + status 的能力。
 * 这些步骤是 demo placeholder，不是任何真实执行记录。
 */
const previewSteps: StepPreview[] = [
  { name: '理解客户请求', status: 'completed' },
  { name: '检索售后政策', status: 'running' },
  { name: '查询订单信息', status: 'pending' },
  { name: '检查库存', status: 'skipped' },
  { name: '评估换货资格', status: 'failed' },
]

function stepStatusLabel(status: AgentStepStatus): string {
  return STEP_STATUS_LABELS[status]
}
</script>

<template>
  <div class="agent-run">
    <a class="back-link" :href="`#/tickets/${ticketKey}`">← 返回工单详情</a>

    <header class="run-header">
      <div class="title-row">
        <h1>Agent Run</h1>
        <span class="shell-tag">UI Shell / 结构预览</span>
      </div>
      <p class="subtitle">
        工单编号 <code>{{ ticketKey }}</code> · Agent 执行结构预览
      </p>
    </header>

    <p class="honest-notice">
      本页面是 Agent Run 的 UI shell / 结构预览，<strong>不是真实执行记录</strong>。
      T010 AgentRun 持久化与 T018 实际执行能力尚未实现，因此当前不存在任何真实的 Agent 执行。
      以下所有内容均为前端结构示意，不会伪造模型输出、工具调用、审批结果或 trace 数据。
    </p>

    <section class="panel" aria-label="执行时间线（结构预览）">
      <h2>执行时间线（结构预览）</h2>
      <p class="section-note">
        时间线 item 支持 step name（步骤名称）与 status（状态）。状态模型支持以下五种状态：
      </p>

      <ul class="status-legend" aria-label="步骤状态图例">
        <li
          v-for="entry in stepStatusLegend"
          :key="entry.status"
          class="legend-item"
          :data-status="entry.status"
        >
          <span class="legend-dot" :data-status="entry.status"></span>
          <span class="legend-label">{{ entry.label }}</span>
          <code class="legend-code">{{ entry.status }}</code>
        </li>
      </ul>

      <p class="placeholder-warning">
        以下步骤为 demo placeholder / structure preview，仅用于展示状态呈现能力，不代表任何真实执行记录。
      </p>

      <ol class="timeline">
        <li v-for="step in previewSteps" :key="step.name" class="timeline-item">
          <span class="timeline-marker" :data-status="step.status"></span>
          <div class="timeline-row">
            <span class="step-name">
              {{ step.name }}
              <span class="step-demo-tag">示例</span>
            </span>
            <span class="step-status" :data-status="step.status">
              {{ stepStatusLabel(step.status) }}
            </span>
          </div>
        </li>
      </ol>
    </section>

    <section class="panel" aria-label="Tool calls">
      <h2>Tool calls</h2>
      <p class="unavailable">当前没有真实的 Tool call 记录。</p>
      <p class="section-note">
        此区域为未来工具调用（如 get_order、check_inventory、create_replacement、update_ticket）的预留展示位置。
        在 T010 / T018 实现前，这里不会出现任何 Tool 输入或输出。
      </p>
    </section>

    <section class="panel" aria-label="人工审批">
      <h2>人工审批（Approval）</h2>
      <p class="unavailable">当前没有真实的审批请求或审批结果。</p>
      <p class="section-note">
        此区域为未来人工审批状态的预留展示位置。当前不存在任何审批请求、批准或拒绝决定。
      </p>
    </section>

    <section class="panel" aria-label="Trace metadata">
      <h2>Trace metadata</h2>
      <p class="unavailable">真实 trace metadata 尚不可用。</p>
      <p class="section-note">
        以下字段不会在本页面被伪造：AgentRun id、模型响应、token、cost、timing。
        待 T010 持久化与 T026 trace 能力实现后，这里才会展示真实数据。
      </p>
    </section>

    <a class="back-link" :href="`#/tickets/${ticketKey}`">← 返回工单详情</a>
  </div>
</template>

<style scoped>
.agent-run {
  font-family: system-ui, -apple-system, sans-serif;
  max-width: 960px;
  margin: 0 auto;
  padding: 2rem;
}

.back-link {
  display: inline-block;
  margin-bottom: 1.25rem;
  color: #3949ab;
  text-decoration: none;
}

.back-link:hover {
  text-decoration: underline;
}

.run-header h1 {
  margin: 0;
  font-size: 1.5rem;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.shell-tag {
  font-size: 0.7rem;
  font-weight: 600;
  color: #455a64;
  background: #eceff1;
  border: 1px solid #b0bec5;
  border-radius: 4px;
  padding: 0.15rem 0.5rem;
  white-space: nowrap;
}

.subtitle {
  margin: 0.4rem 0 0;
  color: #616161;
  font-size: 0.9rem;
}

.honest-notice {
  margin: 1rem 0 1.25rem;
  padding: 0.75rem 0.9rem;
  font-size: 0.88rem;
  line-height: 1.6;
  color: #455a64;
  background: #eceff1;
  border: 1px solid #b0bec5;
  border-radius: 6px;
}

.panel {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 1.25rem;
  margin-bottom: 1.25rem;
  background: #fafafa;
}

.panel h2 {
  margin: 0 0 0.75rem;
  font-size: 1.05rem;
}

.section-note {
  margin: 0 0 0.75rem;
  color: #616161;
  font-size: 0.9rem;
  line-height: 1.6;
}

.unavailable {
  margin: 0 0 0.5rem;
  color: #455a64;
  font-weight: 600;
}

.placeholder-warning {
  margin: 1rem 0 0.75rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.82rem;
  color: #8d6e00;
  background: #fff8e1;
  border: 1px solid #e6c94f;
  border-radius: 6px;
}

.status-legend {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 0 0 1rem;
  padding: 0;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
}

.legend-dot {
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 50%;
  background: #90a4ae;
}

.legend-dot[data-status='running'] {
  background: #1e88e5;
}

.legend-dot[data-status='completed'] {
  background: #2e7d32;
}

.legend-dot[data-status='failed'] {
  background: #c62828;
}

.legend-dot[data-status='skipped'] {
  background: #9e9e9e;
}

.legend-code {
  font-size: 0.75rem;
  color: #757575;
}

.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.timeline-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 0;
  border-bottom: 1px solid #eeeeee;
}

.timeline-item:last-child {
  border-bottom: none;
}

.timeline-marker {
  flex: none;
  width: 0.75rem;
  height: 0.75rem;
  border-radius: 50%;
  background: #90a4ae;
}

.timeline-marker[data-status='running'] {
  background: #1e88e5;
}

.timeline-marker[data-status='completed'] {
  background: #2e7d32;
}

.timeline-marker[data-status='failed'] {
  background: #c62828;
}

.timeline-marker[data-status='skipped'] {
  background: #9e9e9e;
}

.timeline-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex: 1;
  gap: 1rem;
}

.step-name {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.95rem;
}

.step-demo-tag {
  font-size: 0.65rem;
  font-weight: 600;
  color: #8d6e00;
  background: #fff3cd;
  border: 1px solid #e6c94f;
  border-radius: 4px;
  padding: 0.05rem 0.35rem;
  white-space: nowrap;
}

.step-status {
  font-size: 0.8rem;
  font-weight: 600;
  color: #90a4ae;
}

.step-status[data-status='running'] {
  color: #1e88e5;
}

.step-status[data-status='completed'] {
  color: #2e7d32;
}

.step-status[data-status='failed'] {
  color: #c62828;
}

.step-status[data-status='skipped'] {
  color: #9e9e9e;
}
</style>
