# ERPi Pro — Master Checkpoint & AI Handoff (`v2.9.0`)

> **AI Context Directive:** Paste this entire file at the start of any new AI session. Maintain strict Arabic explanations with English technical terms, code snippets, and file paths. Do not re-suggest completed `v2.9.0` tasks; continue directly from **Section 6 (Next Steps)**.

---

## 1. Project Identity & Environment
- **System Name:** ERPi Pro (`erp-backbone`)
- **Current Release:** `v2.9.0` (Finalized on `2026-11-22`)
- **Workspace Root (WSL):** `/mnt/c/Users/SNW5578/Coding/ERPi_Pro_v2_8_2`
- **Backend Path:** `/mnt/c/Users/SNW5578/Coding/ERPi_Pro_v2_8_2/erp-backbone`
- **Frontend Path:** `/mnt/c/Users/SNW5578/Coding/ERPi_Pro_v2_8_2/erp-backbone/frontend`
- **Tech Stack:**
  - **Backend:** Python 3.11 (`.venv`), Django, Django REST Framework, PostgreSQL (test DB `test_erp_backbone`), Celery.
  - **Frontend:** Vue 3, TypeScript, Vite, ECharts, Playwright (E2E), strict RTL/LTR bilingual support (`ar` / `en`).
- **Git Repository State:**
  - Initialized at `/mnt/c/Users/SNW5578/Coding/ERPi_Pro_v2_8_2/.git`
  - Branch: `master` | Tag: `v2.9.0` | Clean working tree.

---

## 2. Completed Milestones in `v2.9.0`

### A. Frontend UI & UX Enhancements (`erp-backbone/frontend`)
1. **Global UI Zoom (`85%` – `125%`):**
   - Implemented persistent zoom state across the Vue 3 workspace (`App.vue`, `style.css`, `navigation.ts`).
   - Added header toolbar zoom controls (`-`, `100%` reset, `+`) and keyboard shortcuts (`Ctrl + +/-/0`) working seamlessly in both RTL (`ar`) and LTR (`en`).
2. **Advanced Bilingual Documentation Lightbox:**
   - Upgraded screenshot preview modal in `PageGuidance.vue` and `TrainingCenter.vue`.
   - Supports interactive zoom/pan, keyboard navigation (`Esc`, `ArrowLeft`, `ArrowRight`), and bilingual captions (`ar` / `en`) across all **242** documentation screenshots in `erp-backbone/backbone/static/docs/screenshots/`.
3. **Frontend Verification Passed:**
   - `npm run check:i18n` (100% key parity between `ar.json` and `en.json`).
   - `npx vue-tsc --noEmit` (zero TypeScript errors).
   - `npm run build` (clean production bundle).
   - Playwright E2E verification in both `en` and `ar` locales.

### B. Backend Core & Modular Compatibility (`erp-backbone`)
1. **Platform Version Bump (`2.9.0`):**
   - Updated `platform_version` to `"2.9.0"` in `erp-backbone/backbone/compatibility.py`.
   - Updated `erp-backbone/.erp-release` to `2.9.0`.
2. **Module Manifest Compatibility (`<3.0`):**
   - Updated `core_version_range` from `,<2.9` to `,<3.0` across all **11** modular `manifest.json` files:
     - `modules/bank_reconciliation/manifest.json`
     - `modules/billing_core/manifest.json`
     - `modules/expenses/manifest.json`
     - `modules/helpdesk/manifest.json`
     - `modules/marketing/manifest.json`
     - `modules/media_service/manifest.json`
     - `modules/parties/manifest.json`
     - `modules/public_identity/manifest.json`
     - `modules/publication/manifest.json`
     - `modules/scheduling/manifest.json`
     - `modules/subscriptions/manifest.json`
3. **Test Suite & Production Readiness:**
   - Resolved test database migration dependencies (`parties` app & manifest range filters).
   - Executed `python manage.py test --noinput`: **All 87 tests passed (`OK` in 49.4s)**.
   - Executed `python manage.py collectstatic --noinput`: **276 static files copied** to `erp-backbone/staticfiles`.
   - Executed `python manage.py erp_preflight`: **`Module compatibility preflight passed.`**

---

## 3. Architecture & Active Modules Map (19 Modules)
All modules reside under `erp-backbone/modules/` and follow the strict module contract (`backbone/module_contract.py`):
- **Core Financials:** `accounting` (GL, budgeting, cost centers, payables), `billing_core`, `bank_reconciliation`, `expenses`, `subscriptions`.
- **Operations & Supply Chain:** `inventory`, `purchasing`, `sales`, `supply`, `manufacturing`, `assets`, `workorders`.
- **People, CRM & Services:** `hr` (attendance, directory, payroll), `crm`, `parties`, `helpdesk`, `projects`, `scheduling`.
- **Digital & Portal:** `marketing`, `publication`, `public_identity`, `media_service`.

---

## 4. Key Operational Commands (Quick Reference)
- Activate environment: `cd /mnt/c/Users/SNW5578/Coding/ERPi_Pro_v2_8_2/erp-backbone && source ../.venv/bin/activate`
- Run backend tests: `python manage.py test --noinput`
- Check module compatibility: `python manage.py erp_preflight && python manage.py audit_modules`
- Frontend build & i18n check: `cd frontend && npm run check:i18n && npx vue-tsc --noEmit && npm run build`
- Start local WSL server: `cd /mnt/c/Users/SNW5578/Coding/ERPi_Pro_v2_8_2 && ./start_erp_wsl.sh`

---

## 5. Rules & Constraints for Any AI Assistant
1. **Language:** Communicate explanations, decisions, and summaries in clear **Arabic**; keep all code, CLI commands, identifiers, and file paths in **English**.
2. **Bilingual Parity:** Any UI change in `frontend/src/` MUST update both `ar.json` and `en.json` and respect RTL layout alignment.
3. **Module Contract:** Never break `core_version_range` (`>=2.x,<3.0`) or cross-module boundaries enforced by `erp_preflight` and `audit_modules`.
4. **No Redundant Setup:** Git is already initialized and tagged at `v2.9.0`. Do not run `git init` again.

---

## 6. Next Steps & Roadmap (`v2.10.0` Candidates)
1. **Inspect `PRODUCT_ROADMAP.md` & `erpi_pro_master_handoff_roadmap.md`** to select the next feature batch for `v2.10.0`.
2. **Create `dev/v2.10.0` branch** (`git checkout -b dev/v2.10.0`) before making new code modifications so `v2.9.0` remains untouched on `master`.
3. **Potential Focus Areas for `v2.10.0`:**
   - Cross-module analytics & `DashboardStudio` custom widget enhancements.
   - Automated PDF/print template refinements (`Noto Arabic` RTL rendering verification across invoices, payslips, and POs).
   - Extended Playwright E2E coverage for `UI Zoom` persistence and `Lightbox` interactions across all 19 modules.
