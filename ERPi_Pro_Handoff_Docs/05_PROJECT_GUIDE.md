# ERPi Pro — Developer & Operations Guide (`v2.11.0`)

## 1. Environment & Directory Layout
```text
ERPi_Pro/
├── ERPi_Pro_Handoff_Docs/  # Core handoff, checkpoint, architecture, foundation & QA docs
├── *.md                    # Root checkpoints, Arabic operator/contract guides & roadmaps
├── start_erp_wsl.sh        # Local WSL deployment & upgrade helper
├── requirements.txt        # Points to -r erp-backbone/requirements.txt
└── erp-backbone/
    ├── backbone/           # Django project settings, URLs, compatibility, print_i18n
    ├── modules/            # Modular Django apps (accounting, inventory, pos, hr, etc.)
    ├── frontend/
    │   ├── scripts/        # i18n validation & build helper scripts
    │   ├── src/locales/    # 9 language JSON files + help.json
    │   ├── src/i18n.ts     # Localization & RTL/LTR runtime
    │   ├── src/navigation.ts
    │   └── src/*.vue       # Module pages & reusable components
    ├── staticfiles/        # Collected production static assets
    └── manage.py
```

## 2. Common Commands
- **Validate Translations:**
```bash
cd erp-backbone/frontend && npm run check:i18n
```
- **Type-Check & Build Frontend:**
```bash
cd erp-backbone/frontend && npm run build
```
- **Audit Contracts, Collect Static & Run Server:**
```bash
source .venv/bin/activate
set -a && source erp-backbone/.env && set +a
python erp-backbone/manage.py check
python erp-backbone/manage.py audit_modules
python erp-backbone/manage.py collectstatic --noinput
python erp-backbone/manage.py runserver 0.0.0.0:8000
```
- **Run Backend Tests:**
```bash
python erp-backbone/manage.py test
```

## 3. How to Use This Handoff Pack with Any AI
Point your AI assistant to `ERPi_Pro_Handoff_Docs/` and prompt:
> *"Read `01_SESSION_HANDOFF.md`, `02_CHECKPOINT.md`, `03_ARCHITECTURE.md`, and `04_FOUNDATION.md` first, then inspect `erp-backbone/` to perform a deep logic review and continue development on `v2.11.0`."*
