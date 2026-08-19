<script setup lang="ts">
import { ref } from 'vue'

const appName = ref('Flyweave')
</script>

<template>
  <div class="flyweave-app">
    <header class="app-header">
      <h1>{{ appName }}</h1>
      <p>企业 AI 服务工作流演示</p>
    </header>
  </div>
</template>

<style scoped>
.flyweave-app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.app-header {
  padding: 2rem;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  text-align: center;
}

.app-header h1 {
  margin: 0;
  font-size: 2.5rem;
  font-weight: 600;
}

.app-header p {
  margin: 0.5rem 0 0;
  font-size: 1rem;
  opacity: 0.9;
}
</style>
