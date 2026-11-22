<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type UserProfile } from './api'
import { message, t } from './i18n'
const emit = defineEmits<{saved: [name: string, picture: string | null]}>()
const profile = ref<UserProfile>()
const busy = ref(false)
const error = ref('')
const saved = ref(false)
const file = ref<File>()
onMounted(async () => {
  try { profile.value = await api<UserProfile>('/profile') }
  catch (failure) { error.value = message(failure) }
})
async function save() {
  if (!profile.value || busy.value) return
  busy.value = true
  error.value = ''
  saved.value = false
  const { first_name, middle_name, last_name } = profile.value
  try {
    profile.value = await api<UserProfile>('/profile','PATCH',{first_name,middle_name,last_name})
    emit('saved', profile.value.display_name, profile.value.picture_url)
    saved.value = true
  } catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
}
function choosePicture(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0]
}
async function updatePicture(remove = false) {
  if (!profile.value || busy.value || (!remove && !file.value)) return
  busy.value = true
  error.value = ''
  saved.value = false
  const data = new FormData()
  if (file.value && !remove) data.append('picture', file.value)
  try {
    const result = await api<{ picture_url: string | null }>('/profile/picture', remove ? 'DELETE' : 'POST', remove ? undefined : data)
    profile.value.picture_url = result.picture_url
    file.value = undefined
    emit('saved', profile.value.display_name, result.picture_url)
    saved.value = true
  } catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
}
</script>

<template>
  <h1>{{ t('User profile') }}</h1>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="saved" class="success" role="status">{{ t('Changes saved.') }}</p>
  <form v-validated-form v-if="profile" class="panel record-form" @submit.prevent="save">
    <div class="field-wrap"><label>{{ t('Username') }}<input :value="profile.username" readonly></label><FieldHint :label="t('Username')" readonly /></div>
    <div class="field-wrap"><label>{{ t('First name') }}<input data-field="first_name" :placeholder="t('Enter {field}').replace('{field}', t('First name'))" v-model="profile.first_name" autocomplete="given-name" maxlength="150"></label><FieldHint :label="t('First name')" /></div>
    <div class="field-wrap"><label>{{ t('Middle name') }}<input data-field="middle_name" :placeholder="t('Enter {field}').replace('{field}', t('Middle name'))" v-model="profile.middle_name" autocomplete="additional-name" maxlength="150"></label><FieldHint :label="t('Middle name')" /></div>
    <div class="field-wrap"><label>{{ t('Last name') }}<input data-field="last_name" :placeholder="t('Enter {field}').replace('{field}', t('Last name'))" v-model="profile.last_name" autocomplete="family-name" maxlength="150"></label><FieldHint :label="t('Last name')" /></div>
    <div class="field-wrap" v-if="profile.employee_number"><label>{{ t('Employee number') }}<input :value="profile.employee_number" readonly></label><FieldHint :label="t('Employee number')" readonly /></div>
    <button class="primary" :disabled="busy">{{ t('Save changes') }}</button>
  </form>
  <form v-validated-form v-if="profile" class="panel record-form profile-details" @submit.prevent="updatePicture()">
    <h2>{{ t('Profile picture') }}</h2>
    <img v-if="profile.picture_url" :src="profile.picture_url" alt="" class="profile-picture-preview">
    <div class="field-wrap"><label>{{ t('Choose a profile picture') }}<input :key="String(saved)" data-field="picture" type="file" required accept="image/jpeg,image/png,image/webp" @change="choosePicture"></label><FieldHint :label="t('Choose a profile picture')" /></div>
    <small>{{ t('JPEG, PNG or WebP, up to 2 MB.') }}</small>
    <div class="actions">
      <button class="primary" :disabled="busy">{{ t('Upload picture') }}</button>
      <button v-if="profile.picture_url" type="button" class="secondary" :disabled="busy" @click="updatePicture(true)">{{ t('Remove picture') }}</button>
    </div>
    <RouterLink v-if="profile.employee_id" :to="'/hr/employees/' + profile.employee_id">{{ t('Employee file') }} · {{ profile.employee_number }}</RouterLink>
  </form>
</template>
