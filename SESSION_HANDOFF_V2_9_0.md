# ERPi Pro v2.9.0 — Session Handoff

- **Release Version:** `2.9.0` (`erp-backbone/.erp-release`, `backbone/compatibility.py`)
- **Date:** 2026-11-22
- **Git Tag:** `v2.9.0`

## Summary of Changes

1. **Global UI Zoom (`85%` – `125%`):**
   - Added persistent zoom controls (`Ctrl + +/-/0` and header toolbar buttons) across the Vue 3 workspace with RTL/LTR parity.
2. **Bilingual Documentation Lightbox:**
   - Upgraded screenshot preview modal in `PageGuidance.vue` / `TrainingCenter.vue` with zoom/pan, keyboard navigation, and bilingual (`en` / `ar`) captions across all 242 documentation screenshots.
3. **Platform & Module Compatibility (`2.9.0`):**
   - Updated `platform_version` to `2.9.0` in `backbone/compatibility.py`.
   - Updated `core_version_range` from `>=2.x,<2.9` to `>=2.x,<3.0` across 11 modular `manifest.json` files (`bank_reconciliation`, `billing_core`, `expenses`, `helpdesk`, `marketing`, `media_service`, `parties`, `public_identity`, `publication`, `scheduling`, `subscriptions`).
4. **Verification & Release Readiness:**
   - Frontend: `check:i18n`, `vue-tsc`, `npm run build`, and Playwright E2E checks (`en` / `ar`) passed.
   - Backend: All 87 Django test suites (`python manage.py test`) passed (`OK`).
   - Production: `collectstatic` (276 static files) and `python manage.py erp_preflight` passed cleanly.
