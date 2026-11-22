<script setup lang="ts">
import { t } from './i18n'
import type { CustomerDraft } from './crm'

defineProps<{busy:boolean;direct:boolean}>()
const fields = defineModel<CustomerDraft>({required:true})
const emit = defineEmits<{submit:[];cancel:[]}>()
</script>

<template>
  <form v-validated-form class="record-form" @submit.prevent="emit('submit')">
    <h3>{{ t(direct ? 'Create Sales customer' : 'Request new customer') }}</h3>
    <div class="crm-fields">
      <label>{{ t('Name') }}<input v-model.trim="fields.name" data-field="name" required maxlength="120" :placeholder="t('e.g., John Doe')"></label>
      <label>{{ t('Legal name') }}<input v-model.trim="fields.legal_name" data-field="legal_name" maxlength="120" :placeholder="t('Company registered name')"></label>
      <label>{{ t('Contact') }}<input v-model.trim="fields.contact" data-field="contact" maxlength="120" :placeholder="t('e.g., name@example.com')"></label>
      <label>{{ t('Tax ID') }}<input v-model.trim="fields.tax_id" data-field="tax_id" maxlength="60" :placeholder="t('Enter tax registration number')"></label>
      <label>{{ t('Billing address') }}<input v-model.trim="fields.billing" data-field="billing" maxlength="240" :placeholder="t('Street, city, country')"></label>
      <label>{{ t('Shipping address') }}<input v-model.trim="fields.shipping" data-field="shipping" maxlength="240" :placeholder="t('Street, city, country')"></label>
      <label>{{ t('Payment terms (days)') }}<input v-model.number="fields.terms" data-field="terms" type="number" min="0" max="365" required></label>
    </div>
    <p class="muted">{{ t('The customer code is assigned automatically.') }}</p>
    <div class="actions">
      <button class="primary" :disabled="busy">{{ t(direct ? 'Create Sales customer' : 'Submit request') }}</button>
      <button type="button" class="secondary" @click="emit('cancel')">{{ t('Cancel') }}</button>
    </div>
  </form>
</template>
