# ERPi Pro — Developer & Operations Guide

## 1. Environment & Directory Layout
```text
erp-backbone/
├── backbone/               # Django project settings, URLs, WSGI/ASGI
├── <domain_apps>/          # Modular Django apps (accounting, inventory, pos, hr, etc.)
├── frontend/
│   ├── scripts/            # i18n validation & build helper scripts
│   ├── src/
│   │   ├── locales/        # 9 language JSON files + help.json
│   │   ├── i18n.ts         # Localization & RTL/LTR runtime
│   │   ├── navigation.ts   # Sidebar navigation tree
│   │   └── *.vue           # Module pages & reusable components
│   └── package.json
├── staticfiles/            # Collected production static assets
└── manage.py
```

## 2. Common Commands
- **Validate Translations:**
```bash
cd frontend && npm run check:i18n
```
- **Type-Check & Build Frontend:**
```bash
cd frontend && npm run build
```
- **Collect Static Files & Run Server:**
```bash
python manage.py collectstatic --noinput
python manage.py runserver 0.0.0.0:8000
```
- **Run Backend Tests:**
```bash
python manage.py test
```

## 3. How to Use This Handoff Pack with Any AI
Upload both `ERPi_Pro_Clean_Source.zip` and `ERPi_Pro_Handoff_Docs.zip` to your AI assistant (DeepSeek, Z.ai, ChatGPT, Claude) and prompt:
> *"Read `01_SESSION_HANDOFF.md`, `02_CHECKPOINT.md`, `03_ARCHITECTURE.md`, and `04_FOUNDATION.md` first, then inspect `erp-backbone/` in the source archive to perform a deep logic review and build a complete module/API knowledge map."*
