# ERPi Pro v2.8.2 — Technical Handoff & Current State

## 1. Environment & Workspace Architecture
- **Workspace Root:** `/mnt/c/Users/SNW5578/Coding/ERPi_Pro_v2_8_2` (`C:\Users\SNW5578\Coding\ERPi_Pro_v2_8_2`)
- **Django + Vue App Root:** `/mnt/c/Users/SNW5578/Coding/ERPi_Pro_v2_8_2/erp-backbone`
- **Modules Layout:** All 28 business modules reside under `erp-backbone/modules/<module_name>/`. Both `manage.py` and `backbone/settings.py` prepend `modules/` to `sys.path`, so imports use top-level names (e.g. `from billing_core.models import Invoice`).
- **Deployed WSL Release:** `~/.local/share/erp-backbone-wsl/releases/v0_1/erp-backbone` (served at `http://127.0.0.1:8001` via `bash start_erp_wsl.sh`).
- **Frontend Build:** Windows Node v23.1.0 at `C:\msys64\ucrt64\bin` (`npm run build` -> `frontend/dist`).

## 2. Completed Upgrades
1. **Module Manager Dependency UX (`ModuleCatalog.vue`, `App.vue`, `style.css`):** Topological prerequisite chain resolution, `Required:` / `Required by:` pills across `Card`, `Grid`, and `List` views, 1-click sequential `Enable in order` (`toggleChain`), and 1,728 bilingual (`en`/`ar`) translations.
2. **Command Palette (`Ctrl+K`) Admin Routes (`backbone/services/commands.py`):** Bilingual navigation commands for `/data-import`, `/admin/users`, `/admin/access`, `/admin/sites`, `/admin/documents`, `/admin/delegations`, `/admin/integrations`, `/admin/security`, and `/modules/visibility`.
3. **Site-Scoped Cross-Module Search (`backbone/services/experience.py`):** Enforced site-level visibility (`visible()`, `visible_employees()`, `tickets_for()`, `rows_for()`) across `Invoice`, `ExpenseClaim`, `Contract` (`version__plan`), `Ticket`, `Lead`, `Party`, `Project`, and `Employee`.
4. **Automated Verification (`backbone/test_commands.py`):** Added site-isolation regression test (`test_site_scoped_user_only_sees_records_from_assigned_site`) alongside permission and bilingual command tests.

## 3. Verification & Full Test Command
- `npm run check:i18n`: `1728 paired translations, 77 help topics and 257 field explanations checked.`
- `python manage.py audit_modules`: `28 contracts passed.`
- Full test suite (all 279 tests across `backbone` + 28 modules):
  `apps=$(find modules -mindepth 1 -maxdepth 1 -type d ! -name '__pycache__' -printf '%f\n') && python manage.py test backbone $apps --noinput`
