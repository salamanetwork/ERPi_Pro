import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  resolve:{dedupe:["vue"]},
  base: '/static/',
  plugins: [vue()],
  build: { target: 'es2020' },
})
