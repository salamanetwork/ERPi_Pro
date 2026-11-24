<script setup lang="ts">
import { t } from './i18n'
import {
  currentUnifiedNotice,
  dismissUnifiedNotice,
  effectiveNoticeMode,
  removeUnifiedToast,
  togglePersonalNoticeMode,
  unifiedToastQueue,
} from './useUnifiedNotice'
</script>

<template>
  <!-- UNIFIED NOTIFICATION SYSTEM: POP-UP MODAL MODE -->
  <div
    v-if="currentUnifiedNotice && effectiveNoticeMode === 'popup'"
    class="un-modal-backdrop"
    @click.self="dismissUnifiedNotice"
  >
    <div :class="['un-modal', `un-${currentUnifiedNotice.type}`]" role="alertdialog" aria-modal="true">
      <div class="un-modal-header">
        <div class="un-title-group">
          <span class="un-badge">{{ currentUnifiedNotice.badge }}</span>
          <h3>{{ currentUnifiedNotice.title }}</h3>
        </div>
        <button type="button" class="un-close-x" @click="dismissUnifiedNotice">×</button>
      </div>

      <div class="un-modal-body">
        <div v-if="currentUnifiedNotice.rawCode" class="un-raw-box">
          <span class="un-raw-label">{{ t('System Code') }}:</span>
          <code>{{ currentUnifiedNotice.rawCode }}</code>
        </div>
        <p class="un-explanation">{{ currentUnifiedNotice.explanation }}</p>
        <div v-if="currentUnifiedNotice.hint" class="un-hint">
          <strong>{{ t('Suggested Action') }}:</strong> {{ currentUnifiedNotice.hint }}
        </div>
      </div>

      <div class="un-modal-footer">
        <button type="button" class="un-btn-secondary" @click="togglePersonalNoticeMode('toast')">
          {{ t('Switch to Toast') }}
        </button>
        <button type="button" class="un-btn-primary" @click="dismissUnifiedNotice">
          {{ t('OK') }}
        </button>
      </div>
    </div>
  </div>

  <!-- UNIFIED NOTIFICATION SYSTEM: TOP-ANCHORED TOAST MODE -->
  <div v-if="unifiedToastQueue.length > 0 && effectiveNoticeMode === 'toast'" class="un-toast-container">
    <div
      v-for="item in unifiedToastQueue"
      :key="item.id"
      :class="['un-toast-card', `un-${item.type}`]"
      @click="removeUnifiedToast(item.id)"
    >
      <div class="un-toast-top">
        <div class="un-toast-head-left">
          <span class="un-badge">{{ item.badge }}</span>
          <strong>{{ item.title }}</strong>
        </div>
        <button type="button" class="un-close-x" @click.stop="removeUnifiedToast(item.id)">×</button>
      </div>
      <div v-if="item.rawCode" class="un-toast-code">{{ t('System Code') }}: <code>{{ item.rawCode }}</code></div>
      <div class="un-toast-desc">{{ item.explanation }}</div>
      <div v-if="item.hint" class="un-toast-hint">{{ item.hint }}</div>
    </div>
  </div>
</template>

<style scoped>
.un-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(2, 6, 23, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}
.un-modal {
  width: 480px;
  max-width: 94vw;
  background: #0f172a;
  color: #f8fafc;
  border-radius: 12px;
  border: 2px solid #38bdf8;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.85);
  overflow: hidden;
}
.un-modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  background: rgba(255, 255, 255, 0.04);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.un-title-group {
  display: flex;
  align-items: center;
  gap: 10px;
}
.un-title-group h3 {
  margin: 0;
  font-size: 16px;
  color: #f8fafc;
}
.un-badge {
  font-size: 11px;
  font-weight: 800;
  padding: 3px 8px;
  border-radius: 6px;
  letter-spacing: 0.4px;
}
.un-close-x {
  background: none;
  border: none;
  color: #94a3b8;
  font-size: 20px;
  cursor: pointer;
  line-height: 1;
}
.un-modal-body {
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.un-raw-box {
  background: rgba(0, 0, 0, 0.38);
  padding: 7px 10px;
  border-radius: 6px;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.un-raw-label {
  color: #94a3b8;
}
.un-explanation {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: #f1f5f9;
}
.un-hint {
  background: rgba(56, 189, 248, 0.1);
  border-inline-start: 3px solid #38bdf8;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  color: #bae6fd;
}
.un-modal-footer {
  padding: 12px 18px;
  background: rgba(255, 255, 255, 0.03);
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.un-btn-primary {
  background: #38bdf8;
  color: #0f172a;
  border: none;
  padding: 8px 16px;
  border-radius: 7px;
  font-weight: 700;
  cursor: pointer;
}
.un-btn-secondary {
  background: #334155;
  color: #f8fafc;
  border: none;
  padding: 6px 12px;
  border-radius: 7px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.un-toast-container {
  position: fixed;
  top: 68px;
  inset-inline-end: 20px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 400px;
  max-width: 92vw;
  pointer-events: auto;
}
.un-toast-card {
  background: #0f172a;
  color: #f8fafc;
  border-radius: 10px;
  border: 2px solid #38bdf8;
  padding: 12px 14px;
  box-shadow: 0 14px 36px rgba(0, 0, 0, 0.85);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.un-toast-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.un-toast-head-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.un-toast-code {
  font-size: 12px;
  color: #94a3b8;
}
.un-toast-desc {
  font-size: 13px;
  color: #f8fafc;
  line-height: 1.45;
}
.un-toast-hint {
  font-size: 12px;
  color: #7dd3fc;
}
.un-error,
.un-fault {
  border-color: #ef4444;
}
.un-error .un-badge,
.un-fault .un-badge {
  background: #ef4444;
  color: #fff;
}
.un-warning {
  border-color: #f59e0b;
}
.un-warning .un-badge {
  background: #f59e0b;
  color: #0f172a;
}
.un-confirmation {
  border-color: #3b82f6;
}
.un-confirmation .un-badge {
  background: #3b82f6;
  color: #fff;
}
.un-information {
  border-color: #06b6d4;
}
.un-information .un-badge {
  background: #06b6d4;
  color: #0f172a;
}
.un-saved {
  border-color: #22c55e;
}
.un-saved .un-badge {
  background: #22c55e;
  color: #0f172a;
}
</style>
