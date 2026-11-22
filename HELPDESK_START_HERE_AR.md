# قبول الطابور المشترك في v2.6.1

1. شغّل الترقية كما هو موضح في START_HERE_AR.md وانتظر جاهزية العامل وموزع Outbox.
2. انشر مقالاً من Public Sites وافتح خريطة الموقع. نفّذ اختبار Unpublish وETag في ACCEPTANCE_AR.md.
3. أوقف Redis في بيئة التدريب. اختبر استمرار قراءة المقالات، ثم أرسل تعليقين خلال 30 ثانية على مقال منشور. المتوقع 201 ثم 429.
4. أعد تشغيل Redis. يحتفظ PostgreSQL بمهمة تحديث خريطة الموقع حتى تُنفذ. اختبر وصول إشعار تذكرة Helpdesk للتحقق من عمل الطابور المشترك.


---

# ERPi Pro v2.4.3 — تشغيل Helpdesk

## الترقية على WSL إلى منفذ 8001

أوقف النسخة القديمة بـ **Ctrl+C**، وفك حزمة v2.4.3 في مجلد جديد. من داخل مجلد الحزمة:

```bash
command -v redis-server >/dev/null || (sudo apt-get update && sudo apt-get install -y redis-server)
ERP_WSL_PORT=8001 bash start_erp_wsl.sh --upgrade
```

لو بتستخدم مسار تثبيت مخصص، مرّر نفس المسار القديم بعد `--upgrade`. المشغّل يحفظ نسخة احتياطية، ويفحص توافق الموديولات، ثم يطبق الترحيلات. افتح `http://127.0.0.1:8001` وتأكد من **Version 2.4.3** في الفوتر.

المشغّل يبدأ Redis على loopback، ومعاه Worker وDispatcher. السجلات بجانب مجلد PostgreSQL للتثبيت، بأسماء `redis.log` و`worker.log` و`outbox.log`. لو عندك Redis مخصص، اضبط `ERP_REDIS_URL` قبل التشغيل. خيار `ERP_WSL_OUTBOX=0` يوقف عمليات التسليم محليًا، وتفضل الأحداث معلقة في PostgreSQL.

## تأكيد جاهزية الإشعارات على WSL

في v2.4.2 المشغّل ينفذ استعلامًا ببيانات حساب ERP على منفذ PostgreSQL الخاص، بعد الترحيلات وقبل تشغيل عمليات الإشعارات. الطرفية تعرض المنفذ الفعلي، ثم تعرض رسائل الحالة:

- **Celery Worker ready (queue: notifications)** بعد تأكيد استعداد الـ Worker لاستقبال مهام الطابور.
- **Outbox Dispatcher ready (PostgreSQL and Redis connected)** بعد أول دورة إرسال ناجحة، حتى لو الطابور فاضي.
- **readiness was not confirmed** لو الجاهزية لم تتأكد خلال 25 ثانية. يستمر تشغيل الويب وتتواصل محاولات العمليات التي ما زالت حية. لو العملية خرجت، الطرفية تعرض **exited** ومسار سجلها.

لو ظهر `Connection refused` على 5432 عند التشغيل اليدوي من طرفية ثانية، شغّل ERP بالمشغّل أعلاه. PostgreSQL الخاص يختار منفذًا تلقائيًا، والـ Worker والـ Dispatcher يرثوا إعداداته من نفس المشغّل. مسارات `worker.log` و`outbox.log` تظهر مع نتيجة الجاهزية. للتأكد من التسليم، افتح بلاغًا جديدًا من حساب مختلف عن `manager_test` وراجع **Notifications** من أيقونة الجرس بحساب الموزع.

## تشغيل Docker Compose على WSL2

فعّل تكامل توزيعة WSL2 في Docker Desktop. في Staging قائم، احفظ نسخة من PostgreSQL والمرفقات وفق `DEPLOYMENT_GUIDE_AR.md`. احتفظ بنفس `COMPOSE_PROJECT_NAME` وإعدادات قاعدة البيانات والمرفقات. انسخ ملف `.env.staging` القديم إلى `erp-backbone` داخل الحزمة، واضبط `ERP_RELEASE=2.4.3` وحافظ على مسار شهادات TLS. لتثبيت جديد، استخدم `.env.example` واضبط الأسرار والشهادات أولًا حسب دليل النشر.

```bash
cd erp-backbone
dc() { docker compose --env-file .env.staging -f docker-compose.yml -f deploy/staging.yml "$@"; }
dc config --quiet &&
dc build app &&
dc up -d --wait db &&
dc run --rm --no-deps migrate python manage.py erp_preflight &&
dc up -d
```

بعد تشغيل الحاويات:

```bash
dc ps -a
dc exec redis redis-cli ping
dc logs --tail 50 worker outbox-dispatcher
dc exec nginx nginx -t
curl --fail --show-error --silent --cacert tls/staging/fullchain.pem https://localhost:8443/health/ready
```

نتيجة Redis هي `PONG`، والـ Worker يستمع على `notifications`. حاوية `migrate` تنتهي برمز خروج 0. لو قاعدة البيانات جديدة، أنشئ الأدمن مرة واحدة باستخدام `dc exec app python manage.py setup_backbone`. اتصال Worker داخلي على شبكة Compose بعنوان `redis://redis:6379/0`.

## تشغيل Worker وOutbox Dispatcher يدويًا

حزمة v2.4.0 فيها تطبيق Celery في `backbone/celeryapp.py`. صيغة `-A backbone` تعتمد على اكتشاف التطبيق من اسم الحزمة، وتعمل ابتداءً من v2.4.1 عبر الملف `backbone/celery.py`. الصيغة الصريحة التالية تعمل في الإصدارين.

لو شغلت ERP بمشغّل WSL أعلاه، المشغّل يبدأ العمليتين تلقائيًا. راجع المسارين اللي يطبعهم عند بدء العمليات. للتشغيل اليدوي في بيئة Django معدّة بالفعل، افتح طرفيتين في مجلد `erp-backbone`، فعّل نفس Python venv، واستخدم نفس إعدادات قاعدة البيانات و`ERP_REDIS_URL` في الطرفيتين:

```bash
python -m celery -A backbone.celeryapp:app worker -Q notifications -l info --concurrency=1
```

```bash
python manage.py erp_dispatch_outbox
```

الـ Dispatcher يقرأ عمليات التسليم المعلقة من PostgreSQL ويرسلها إلى طابور `notifications`. الـ Worker ينفذ التسليم. داخل Docker Compose، شغّل الخدمتين بـ `dc up -d redis worker outbox-dispatcher` بعد تهيئة أمر `dc` المذكور أعلاه. من v2.4.1 الطابور الافتراضي هو `notifications`، والصيغة `celery -A backbone worker -l info` تشتغل أيضًا.

اختبر بحساب مختلف عن مقدم البلاغ. حساب الموزع يحتاج `helpdesk.view` و`helpdesk.dispatch` على نفس الموقع. راجع **Notifications** من أيقونة الجرس للإشعار. **Inbox** يعرض مهام الإجراءات. عند وجود عمليات تسليم معلّقة قبل الترقية، يبدأ تسليمها بعد رجوع العمليتين واتصال Redis.

## إعداد Helpdesk

فعّل **Helpdesk** من **Module Manager** وافتحه من **General → Helpdesk**. من **Access control**، عيّن الأدوار على موقع الاختبار. استخدم حسابًا لصاحب البلاغ وحسابًا مختلفًا للموزع لتتأكد من وصول الإشعار.

| الدور | الصلاحيات |
| --- | --- |
| مقدم البلاغ | `helpdesk.view` و`helpdesk.request` |
| مسؤول الدعم | `helpdesk.view` و`helpdesk.agent` |
| موزع البلاغات | `helpdesk.view` و`helpdesk.dispatch` |
| إعادة الفتح | `helpdesk.reopen`، مع إمكانية قراءة البلاغ |
| إعداد التصنيفات والـ SLA | `helpdesk.view` و`helpdesk.configure` |

**New ticket → Open ticket** يفتح البلاغ. **Assign ticket** يحدد المسؤول. **Change status** ينقل البلاغ إلى **In progress** أو **Waiting** أو **Resolved** وفق الحالة الحالية. صاحب البلاغ أو مسؤول الدعم يقدر ينقله من **Resolved** إلى **Closed**. إعادة الفتح تحتاج سببًا وتحافظ على مواعيد الـ SLA الأصلية. في **Comments** اكتب ردًا عامًا أو اختر **Internal note** لو معاك صلاحية الدعم. **Linked records** يربط أمر شغل أو مهمة مشروع في نفس الموقع عند توفر صلاحية السجل المرتبط.

## خصوصية الملاحظات الداخلية

صاحب البلاغ يستقبل الردود العامة فقط في صفحة البلاغ والـ API، حتى لو حسابه إداري أو مكلّف بدور دعم. باقي المستخدمين يحتاجوا دورًا معيّنًا صراحةً يضم `helpdesk.agent` أو `helpdesk.dispatch` على موقع البلاغ أو على كل المواقع. نفس القاعدة بتتحقق عند قراءة سجل البلاغ أو إرسال ملاحظة داخلية.

## SLA وتسليم الإشعارات

| الأولوية | أول رد | الحل |
| --- | --- | --- |
| Low | 4 ساعات | 48 ساعة |
| Normal | ساعتان | 24 ساعة |
| High | ساعة | 8 ساعات |
| Urgent | 30 دقيقة | 4 ساعات |

المدد مستمرة 24/7، بما فيها **Waiting**. أول رد هو أول تعليق عام يكتبه مسؤول دعم غير صاحب البلاغ. إصدار جديد من **Helpdesk settings → SLA policies** يسري على البلاغات المستقبلية. تجاوز الموعد يظهر على البلاغات المفتوحة، ويرسل إشعارًا للموزع والمسؤول المعين إن وجد.

مع توقف Redis، PostgreSQL يحفظ البلاغ والحدث وأهداف التسليم. **Workspace** يبيّن تأخر الإشعارات المعلقة. بعد رجوع Redis، الـ Dispatcher والـ Worker يكملان التسليم. النسخة الاحتياطية لقاعدة البيانات تشمل الـ Outbox.

لتفعيل البريد، اضبط `ERP_SMTP_ENABLED=1` وباقي متغيرات `ERP_SMTP_*`. عنوان SMTP لازم يكون قابلًا للوصول من حاوية **worker**. البريد يضم رقم البلاغ ورسالة عامة. تسليمه قابل لإعادة المحاولة، وقد يتكرر لو استلم مزود البريد الرسالة ثم انقطع الاتصال قبل تسجيل التسليم. إشعار التطبيق له قيد يمنع تكراره.

## توافق الموديولات

`platform_compatibility` يعبّر عن نسخة عقد المنصة، و`core_version_range` يحدد إصدارات التطبيق المتوافقة. **Module Manager** يبيّن عدم التوافق. فحص `python manage.py erp_preflight` يوقف الترقية قبل `migrate` لو فيه موديول مفعّل غير متوافق أو تعريفه مفقود. حدّث الإضافة، أو عطّلها من النسخة القديمة بعد مراجعة أعمالها المفتوحة، وأعد الفحص.

راجع `ACCEPTANCE_AR.md` لدورة اختبار البلاغ وتعطل Redis وتوافق الموديولات.
