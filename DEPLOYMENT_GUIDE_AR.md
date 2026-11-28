> **مرجع الإصدار الحالي (`v2.11.0` — 2026-11-28):** هذا الدليل التخصصي معتمد ضمن حزمة `v2.11.0` (29 عقد موديول، 9 لغات واجهة وطباعة). لحالة التسليم ونقطة الفحص الحالية راجع `CHECKPOINT_V2_11_0.md` و`ERPi_Pro_Handoff_Docs/`.

# ERP Backbone v1.6.0 — حزمة النشر العامة، المرحلة الأولى

الإصدار المرجعي للأعمال هو v1.5.1 المقبول. ابدأ ببيئة **Staging** على Linux داخل WSL2 أو VPS تجريبي. الحزمة فيها إعداد مستقل لبيئة **Production** لاستخدامه لما تتوفر الخوادم والنطاقات. الحزمة مبنية لـLinux x86-64 وتحتاج عند أول Build اتصالًا بمستودعات الصور وDebian وPyPI. الأوامر هنا تتنفذ من مجلد `erp-backbone` داخل الـZIP.

## 1. تجهيز Staging

يلزم Docker Engine وDocker Compose v2، وOpenSSL وcurl وPython 3 وRestic وcron على المضيف. على VPS استخدم SSH tunnel أو عنوان واجهة VPN خاصة. القاعدة وGunicorn على شبكات Compose، وNginx هو نقطة الدخول.

```bash
umask 077
cp .env.example .env.staging
chmod 600 .env.staging
mkdir -p tls/staging
openssl req -x509 -newkey rsa:3072 -sha256 -days 30 -nodes \
  -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost' \
  -keyout tls/staging/privkey.pem -out tls/staging/fullchain.pem
```

ولّد **4 قيم مستقلة** عبر `openssl rand -hex 48`، واحفظها في `ERP_SECRET_KEY` و`ERP_MFA_KEY` و`ERP_POSTGRES_PASSWORD` و`ERP_DB_PASSWORD` في الملف. احتفظ بمفتاح MFA في مخزن الأسرار مع نسخته الخارجية. تغيير كلمات سر PostgreSQL في الملف بعد إنشاء الـvolume يحتاج تعديل حسابات القاعدة نفسها. استخدم مفاتيح منفصلة بين البيئات.

```bash
dc() { docker compose --env-file .env.staging -f docker-compose.yml -f deploy/staging.yml "$@"; }
dc config --quiet
dc build app
dc up -d --wait db
dc run --rm --no-deps app python manage.py check --deploy --fail-level WARNING
dc run --rm --no-deps app python manage.py migrate --noinput
dc run --rm --no-deps app python manage.py collectstatic --noinput
dc run --rm --no-deps app python manage.py setup_backbone
dc up -d --wait app nginx
dc exec nginx nginx -t
curl --cacert tls/staging/fullchain.pem https://localhost:8443/health/ready
curl -Is http://localhost:8080/health/ready
```

المتوقع `ready` عبر HTTPS وتحويل HTTP إلى HTTPS بمنفذ 8443. ثق بشهادة الاختبار محليًا أو استخدم شهادة معتمدة لاسم خاص. عند VPS خليه مربوط على loopback واستخدم مثلًا `ssh -L 8443:127.0.0.1:8443 user@vps`. لفحص الواجهة افتح `https://localhost:8443`، أكمل MFA للأدمن، ثم فعّل الموديولات اللازمة. HSTS يطبق على النطاق وتوابعه. Django يستثني تحذير إدراج المتصفح المسبق افتراضيًا. `ERP_HSTS_PRELOAD=1` يفعّل الهيدر فقط. صفحات التطبيق بتظل محمية بمراجعة صلاحيات Django، والمرفقات تُخدّم من API.

## 2. تجهيز Production عند توفر الخادم

انسخ المثال إلى `.env.production` وحدد `COMPOSE_PROJECT_NAME=erp-production` واسم نطاق HTTPS خاص بالبيئة في `ERP_HOST` و`ERP_AUTHORITY`. اضبط المنافذ 80/443 وعنوان الربط على واجهة VPN أو الشبكة الخاصة، وضع شهادة موثوقة ومفتاحها في `ERP_TLS_DIR`، باسمَي `fullchain.pem` و`privkey.pem`. استخدم شهادة مناسبة للبنية المختارة وجدّدها بخدمة الشهادات عندك ثم نفّذ `dc exec nginx nginx -s reload`. استبدل ملف البيئة والـoverride في تعريف `dc` بـ `.env.production` و`deploy/production.yml`. انقل صورة التطبيق التي اختُبرت في Staging إلى Production بنفس الـtag وبصمة الصورة، وشغّل خطوات الفحص والترحيل وتهيئة أول مستخدم.

اضبط مراقبة خارجية لـ`/health/ready`، وراقب حالة الحاويات والمساحة في volumes. Nginx وGunicorn يتشاركان معرّف الطلب في السجلات. لاختبار العزل، جرّب مستخدمًا من موقع آخر على رابط مستند ومرفق وكشف راتب، وحسابًا غير مخوّل بالرواتب. اقصر الوصول إلى منافذ المضيف عبر firewall على الشبكة الخاصة، وتحقق من جهاز خارجها. عند تحديث قاعدة بيانات قائمة، خذ نسخة كاملة أولًا وقارن إصدار PostgreSQL. إذا حصل فشل، اختبر الإصدار السابق مع قاعدة ومرفقات مسترجعة من نفس نقطة النسخ قبل إعادة الخدمة.

## 3. النسخ الخارجي كل 6 ساعات والاستعادة

1. جهّز bucket منفصلًا لكل بيئة على تخزين S3 متوافق مع HTTPS. حدّد صلاحيات حساب التخزين على مستودع البيئة فقط. انسخ `.env.backup.example` إلى ملف مثل `/secure/erp/staging-backup.env` بصلاحية `600`. خزّن كلمة Restic في ملف منفصل. حضّر المستودع مرة واحدة بالأمر `restic init` بعد تحميل متغيرات الملف في جلسة shell مخصصة عبر `set -a; source /secure/erp/staging-backup.env; set +a`.
2. شغّل `./backup.sh .env.staging /secure/erp/staging-backup.env`. النسخة تشمل dump ومرفقات مع manifest وبصمة لكل ملف، ثم تُحفظ في مستودع Restic المشفّر. المستودع يحتفظ بنقاط 30 يومًا، مع الاحتفاظ بأحدث نقطة. راقب عمر آخر snapshot ناجحة بحيث لا يعدّي 6 ساعات. قالب `deploy/backup.cron.example` يشغّل النسخ كل 6 ساعات، ويحتاج إعداد وصول بريد الأخطاء واختباره. شغّل `restic check` دوريًا وجدول `restic prune` في نافذة مستقلة عن النسخ. مفتاح MFA ومفاتيح الوصول لمستودع النسخ محفوظة خارج الخادم.
3. اختبر الاستعادة بالحساب المخوّل وعلى بيئة معزولة. جهّز Compose على الخادم البديل، استخدم مفتاح **MFA الأصلي**، وشغّل `dc up -d --wait db` وابنِ صورة التطبيق. ثم نفّذ مثلًا:

```bash
time ./restore.sh .env.staging /secure/erp/staging-backup.env latest restore_dr1 restore_dr1
```

لو البيئة المستعادة اسم Compose مختلف، مرّر ملف إعداد البيئة المصدر **كمعامل سادس** لتحديد وسم النسخة. وجهتا الاستعادة جديدتان. بعد اكتمالها اضبط `ERP_DB_NAME=restore_dr1` و`ERP_MEDIA_SUBDIR=restore_dr1` في **البيئة المعزولة**، ونفّذ `migrate` و`collectstatic` ثم `dc up -d --wait app nginx`. سجّل عمر النسخة ووقت الاستعادة الكلي حتى نجاح تسجيل الدخول، وافتح مرفقًا وراجع الأرصدة والقيود. الهدف RPO ≤ 6 ساعات وRTO ≤ ساعتين، ويتأكد بالقياس. لو فشلت الاستعادة جزئيًا استخدم اسم قاعدة ومجلد جديدين في إعادة المحاولة بعد مراجعة الخطأ. لمراجعة احتفاظ المستودع استخدم `restic snapshots`.

### تجربة ترقية بيانات WSL v1.5.1

من التثبيت القديم أنشئ نسخة باستخدام `ERP_WSL_ACTION=backup bash start_erp_wsl.sh`، واحتفظ بمفتاح MFA الفعلي. لو التثبيت استخدم المفتاح الافتراضي المشتق من إعداد Django، استخدم قيمة `django.key` القديمة كـ`ERP_MFA_KEY` في بيئة الترقية مع `ERP_SECRET_KEY` جديد. قبل الاستيراد افحص major نسخة PostgreSQL في `manifest.json`. الحزمة تستهدف PostgreSQL 17، فلو المصدر من major أحدث استخدم PostgreSQL وعميل dump/restore مناسبين على الأقل لنفس major المصدر وخطط للترقية قبل قبولها.

```bash
dc up -d --wait db
dc exec -T db createdb -U postgres --owner=erp_app restore_v151
dc run --rm --no-deps -T app sh -ec '
  temp=$(mktemp -d)
  trap "rm -rf -- \"$temp\"" EXIT
  cat >"$temp/erp.zip"
  python manage.py erp_restore "$temp/erp.zip" --target-db restore_v151 \
    --target-media /srv/erp/media/restore_v151
' < /path/to/v1.5.1-backup.zip
```

عدّل `ERP_DB_NAME=restore_v151` و`ERP_MEDIA_SUBDIR=restore_v151` في Staging وشغّل `migrate` و`collectstatic` ثم `dc up -d --wait app nginx`. تأكد من بقاء رقم دفعة رواتب قديمة وقيدها، وافتح مستندًا ومرفقًا سابقين. راجع تاريخ القطع وميزان المراجعة. احتفظ بمجلد الإصدار السابق وبنسخة المصدر وإعداداتها لتمرين الرجوع.

## 4. اختبار الأداء باستخدام k6

الاختبار يكتب Work Orders اصطناعية. استخدم قاعدة **Benchmark** جديدة على Staging ومشروع Compose مستقل مثل `erp-benchmark` ومنافذ مختلفة أو أوقف Staging أثناء الاختبار. انسخ ملفًا خاصًا واضبط `ERP_INITIAL_DB_NAME=benchmark_erp` و`ERP_DB_NAME=benchmark_erp` و`ERP_MFA_ALL=0` في ملف Benchmark فقط، ثم عرّف `dc` بهذا الملف. طبّق نفس خطوات البناء والترحيل وcollectstatic، وشغّل الأمر التالي **بدل setup_backbone**:

```bash
dc run --rm --no-deps app python manage.py seed_benchmark
dc up -d --wait app nginx
```

الأمر ينشئ أربعة مواقع و100 ألف حركة مخزون و100 ألف سطر قيد، ويطبع الإعدادات اللازمة لحساب الاختبار. سجّل القيم في جلسة k6 الخاصة. ثق بشهادة Staging في مخزن الثقة على جهاز مولّد الحمل قبل التنفيذ. استخدم قيمة `BASE_URL` المطابقة لاسم شهادة الاختبار.

```bash
export BASE_URL=https://localhost:8443 STAGING_ONLY=yes
export K6_USERNAME=benchmark K6_SITE_ID=1 K6_MONTH=2026-11
read -rs -p 'Benchmark password: ' K6_PASSWORD; export K6_PASSWORD; echo
PROFILE=mixed VUS=5 DURATION=30s k6 run deploy/performance/backbone.js
PROFILE=mixed VUS=100 DURATION=30m k6 run deploy/performance/backbone.js | tee k6-mixed.log
PROFILE=rate RATE=20 DURATION=30m k6 run deploy/performance/backbone.js | tee k6-rate.log
```

استبدل رقم الموقع والشهر في المثال بقيم أمر التوليد قبل التنفيذ. فعّل `set -o pipefail` عند استخدام `tee` عشان فشل k6 يظهر كفشل للأمر. الحساب بيسجّل دخول مرة واحدة، ويُستخدم عبر المستخدمين الافتراضيين لقياس مزيج القراءة والتقارير والكتابة؛ قياس تزامن أعمال المخزون وAP منفصل. سجّل النتائج في `deploy/performance/REPORT_TEMPLATE_AR.md`. المستهدف p95 ≤ ثانية للعمليات اليومية، ≤ 3 ثوانٍ للتقارير، وفشل HTTP أقل من 1%، وصفر dropped iterations في اختبار المعدل الثابت. شغّل اختبارات التطبيق على قاعدة PostgreSQL معزولة بمستخدم لديه إنشاء قاعدة اختبارات، باستعمال `backbone.settings` في حاوية اختبار مستقلة. راجع حالات التزامن في `inventory.tests` و`accounting.test_payables` و`sales.tests`.

راجع `DEPLOYMENT_ACCEPTANCE_AR.md` لعلامات النجاح قبل نقل الحزمة إلى Production. أدلة المستخدم الكاملة وتقرير قياس السعة من مخرجات مرحلة القبول اللاحقة.

الخطوات التشغيلية المختصرة للموديولات متاحة في `MODULE_QUICK_REFERENCE_AR.md`.
