<script setup lang="ts">
defineProps<{ ticketKey: string }>()
</script>

<template>
  <div class="ticket-detail-placeholder">
    <a class="back-link" href="#/">返回工单队列</a>
    <h1>工单详情（占位）</h1>
    <p class="ticket-key">工单编号: {{ ticketKey }}</p>
    <p class="placeholder-note">完整的工单详情功能尚未实现，此页面仅用于验证导航可用。</p>
  </div>
</template>

<style scoped>
.ticket-detail-placeholder {
  font-family: system-ui, -apple-system, sans-serif;
  max-width: 960px;
  margin: 0 auto;
  padding: 2rem;
}

.back-link {
  display: inline-block;
  margin-bottom: 1rem;
  color: #3949ab;
  text-decoration: none;
}

.ticket-key {
  font-weight: 600;
}

.placeholder-note {
  color: #616161;
}
</style>
