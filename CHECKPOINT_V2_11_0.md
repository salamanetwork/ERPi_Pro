# ERPi Pro v2.11.0 — Audit & Release Checkpoint

- **Date:** 2026-11-28
- **Branch / Tag:** `master` / `dev/v2.11` (`tag: v2.11.0`, commit `3087920`)
- **Module Contracts:** `29 contracts passed` (`python manage.py audit_modules`, `RELEASE_VERSION = "2.11.0"`)
- **Localization Gate:** `npm run check:i18n` passed (`1,988 paired translations across [en, ar, ur, fa, fr, es, de, tr, hi], 77 help topics and 259 field explanations checked`)
- **Frontend Components:** `88 active Vue components` in `erp-backbone/frontend/src/`

## 1. Key Deliverables in `v2.11.0`

1. **9-Locale Document Printing Engine (`backbone/print_i18n.py`):** Localized all printable document templates (`document_print.html`, `billing_core`, `hr/payslip.html`, `inventory`, `purchasing`, `sales`) with automatic `RTL`/`LTR` layout direction across all 9 locales.
2. **Company Legal Identity (`CompanyProfile`):** Added Tax Registration Number (TRN), Commercial Registration (CR), default currency, and street address (`0023_companyprofile_address_and_more.py` + `CompanyProfilePage.vue`) and rendered them across printable document headers.
3. **UI Notice Polish:** Fixed `useUnifiedNotice.ts` so internal `__auto__` markers never render in toast notifications.
4. **Release Version Synchronization:** Unified `RELEASE_VERSION = "2.11.0"`, `ERP_RELEASE=2.11.0`, and `modules/pos/manifest.json` (`2.11.0`).

## 2. Commit History (`v2.10.2` -> `v2.11.0`)

| Commit | Scope | Summary |
| :--- | :--- | :--- |
| `3087920` | Release sync (`v2.11.0`) | Synchronize release version to `2.11.0` across backend, Docker/env, and POS manifest |
| `bc6af18` | Print i18n | Support all 9 system locales and `RTL`/`LTR` direction across print templates |
| `3dc92fb` | UI notices | Prevent internal `__auto__` marker from rendering in toast notices |
| `2086d09` | Tests | Verify company identity fields render in print templates |
| `ea73ae2` | Print templates | Render company identity, tax, CR, and address across printable documents |
| `0c1ccc5` | Frontend UI | Wire company tax, commercial registration, currency, and address fields |
| `179f730` | Backend model | Add TRN, commercial registration, default currency, and address to `CompanyProfile` |
