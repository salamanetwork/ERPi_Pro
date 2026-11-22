# ERPi Pro v2.5.1 Development — Marketing

الحزمة مبنية على v2.4.3 وتشمل دليل قبول Marketing في `ACCEPTANCE_AR.md`. راجع دليل Helpdesk المرفق لتشغيله بجانب Marketing.

## إصلاح إجراءات الحملة في v2.5.1

افتح **Marketing → Campaigns** واضغط عنوان الحملة أو **Details**. صفحة التفاصيل المستقلة `/marketing/campaigns/{id}` تعرض **Preview** و**Test send** و**Approve** و**Send now** حسب صلاحيات المستخدم والحالة.

- **Preview** يحتاج `marketing.edit`.
- **Test send** و**Approve** و**Send now** تحتاج `marketing.admin` على الموقع.
- لو أنت منشئ الحملة، زر **Approve** يظهر معطلًا مع توضيح إن مستخدمًا آخر لازم يعتمدها. استخدم حسابًا آخر بدور `marketing.admin` على نفس الموقع.
- **Send now** يتاح بعد الاعتماد، ويسجل وقت UTC الحالي كموعد تشغيل ويحوّل الحالة إلى **scheduled**. الـ Dispatcher والـ Worker ينفذوا التسليم من الطابور. **Schedule** يتيح اختيار موعد لاحق.

الترقية من v2.5.0 تستخدم نفس أمر WSL المذكور أدناه. الإصدار ده يحافظ على القاعدة الحالية ويضيف إجراءات العرض والإرسال، وفحص الترحيلات يرجع **No changes detected**.

### API لإكمال الاختبار

| الطلب | البيانات المطلوبة |
| --- | --- |
| `GET /api/marketing/campaigns/{id}` | يعرض الحالة و`revision` و`actions` و`is_creator`. |
| `POST /api/marketing/campaigns/{id}/preview` | `{}` — معاينة ببيانات فعلية. |
| `POST /api/marketing/campaigns/{id}/test-send` | `{"address":"test@example.test","contact_id":1}` — استبدل معرّف الجهة بجهة العينة. |
| `POST /api/marketing/campaigns/{id}/approve` | `{"revision":1}` — حساب معتمد مختلف عن المنشئ. |
| `POST /api/marketing/campaigns/{id}/send` | `{"revision":2}` — v2.5.1 يتيح الإرسال الآن. استخدم أحدث revision من GET. |
| `POST /api/marketing/campaigns/{id}/schedule` | `{"revision":2,"scheduled_at":"2026-11-17T22:30:00+04:00"}` — موجود في v2.5.0 للإرسال في الموعد المحدد أو موعد الآن. |

طلبات الكتابة تحتاج جلسة دخول صالحة، وCSRF للطلبات HTTP، وترويسة `Idempotency-Key` مختلفة لكل إجراء جديد. إعادة نفس الإجراء بنفس المفتاح ونفس الجسم ترجع النتيجة السابقة. استخدم إعدادات قاعدة البيانات الحالية لو بتشتغل من Django Shell. القيم الرقمية في المثال مجرد أمثلة.

## التشغيل المحلي على WSL2

شغّل Mailpit في طرفية. إعداد SMTP المحلي يلتقط الرسالة في الصندوق، ويستخدم الإعداد بدون ترحيل إلى مزود خارجي.

```bash
docker run --rm --name erpi-mailpit \
  -p 127.0.0.1:1025:1025 -p 127.0.0.1:8025:8025 \
  axllent/mailpit:v1.33.1 --disable-version-check
```

أوقف الإصدار السابق بـ **Ctrl+C**، ثم فك الحزمة في مجلد جديد وشغّل الأمر التالي من داخله. أضف مسار التثبيت السابق في آخر الأمر لو كنت بتستخدم مسارًا مخصصًا.

```bash
command -v redis-server >/dev/null || (sudo apt-get update && sudo apt-get install -y redis-server)
ERP_WSL_PORT=8001 bash start_erp_wsl.sh --upgrade
```

افتح ERPi Pro على `http://127.0.0.1:8001` وMailpit على `http://127.0.0.1:8025`. المشغّل يجهز نسخة احتياطية، يطبق الترحيلات، ويشغّل Dispatcher وWorker لطابوري `notifications` و`marketing`. المنفذ `1025` هو SMTP. إعداد WSL يفعّل Mailpit تلقائيًا عند أول استخدام للبريد، والقنوات SMS وWhatsApp في WSL تستخدم محول اختبار محلي.

عن طريق **Module Manager** فعّل **Marketing** واختر الموقع. الترحيل يعيّن دور `marketing.admin` للحسابات الإدارية الموجودة. من **Access control** عيّن مستخدمًا آخر بدور `marketing.editor` على موقع الاختبار. المحرر ينشئ الحملة، والأدمن يعتمدها ويختبرها ويجدولها. قراءة تقرير **Suppressions** تحتاج `marketing.suppressions`. حسابات المصدر تحتاج صلاحية CRM أو Sales حسب المرجع المستخدم.

## أول مسار محلي

1. من **Marketing → Settings** اختر **Double Opt-in** واحفظ.
2. بحساب المحرر افتح **Contacts** وأضف جهة باسم Ahmed وبريدًا تجريبيًا مثل `ahmed@example.test`. افتح **Details** ثم **Opt in**. الحالة `pending`.
3. افتح رسالة التأكيد في Mailpit، وافتح الرابط واضغط **Confirm subscription**. الحالة تتحول إلى `confirmed`. الرابط ينتهي خلال 48 ساعة. تأكيد جهة اتصال لقناة رقمية يتطلب بريدًا على نفس جهة الاتصال، وتُسجل الموافقة للقناة التي طلبت التأكيد.
4. أنشئ شريحة من معرّفات جهات الاتصال، وقالب Email بعنوان `Hello {{first_name}}`، ثم أنشئ الحملة. افتح **Preview** داخل Marketing لرؤية عينة حتى خمس جهات مطابقة. لو متغير ناقص يظهر تحذير داخل العينة.
5. بحساب الأدمن اضغط **Approve** ثم **Test send**، واختر Contact ID للمعاينة وعنوان اختبار مثل `test@example.test`. راجع الرسالة في Mailpit و**Test delivery status** في النظام. مؤشرات الحملة ما تتغيرش من إرسال الاختبار.
6. اختَر وقت **Schedule** حسب المنطقة المعروضة للموقع، وجدول الحملة. لو منطقة الموقع اتغيرت بعدها، النظام يعرض التنبيه والوقت المحلي الجديد. إعادة الجدولة تكون من نفس الشاشة. وقت UTC المعتمد يظل صالحًا لحين التعديل.
7. من **Suppressions** راجع القناة والسبب والتاريخ. **Dead letters** يعرض الإخفاقات النهائية وإجراء إعادة الطابور مع فحص الأهلية.

## إعداد SES في بيئة تحقق بتحكم المشغل

1. في AWS Console افتح **SES → Verified identities → Create identity**، اختَر **Domain** في الـ Region المخصص واختر **Easy DKIM**. انشر سجلات CNAME اللي يطلعها SES، وانتظر التحقق وحالة DKIM **SUCCESS**.
2. لو `From` هو `campaigns@example.com`، تحقّق من توقيع DKIM على `example.com` واستخدم Custom MAIL FROM مثل `bounce.example.com`. SES يتطلب أن يكون Custom MAIL FROM نطاقًا فرعيًا للدومين الموثق. انشر سجلي MX وTXT (SPF) للـ **MAIL FROM** بالنص اللي يولده SES. اختَر رفض الرسالة عند فشل MX لتفادي استخدام MAIL FROM افتراضي.
3. انشر سياسة TXT على `_dmarc.example.com` واستخدم SPF alignment مرنًا `aspf=r` مع هذا الترتيب. افحص المحاذاة والسجلات من صفحة الحملة عبر **Provider preflight**. في وضع SES، الجدولة والاستئناف يتوقفوا لو فشل التحقق، والـ Worker يعيد الفحص قبل التسليم.
4. اعمل **Configuration Set** وSNS Topic بتوقيع **v2**. اختر حدث Delivery وDelivery Delay وBounce وComplaint عند إعداد Destination. ربط الحساب في **Marketing → Settings** يحدد Region وConfiguration Set وTopic ARN. أضف اشتراك HTTPS على `/api/marketing/webhooks/ses/{provider_id}` بعد تسجيل الحساب. صلاحيات خدمة AWS تشمل قراءة حالة الحساب والهوية، والإرسال، وتأكيد الاشتراك في الـ Topic المحدد. افصل بيئة الاختبار عن حساب الإرسال الفعلي حسب سياسة التشغيل.
5. لاختبار SES Sandbox استخدم **Test send** لعنوان موثّق أو Mailbox Simulator من القائمة المسموحة `ERP_MARKETING_TEST_RECIPIENTS`. افتح رسالة اختبار مستلمة وتحقق من `Authentication-Results`، وغطاء ترويستي `List-Unsubscribe` و`List-Unsubscribe-Post` في `DKIM-Signature`. عملية Test send تستخدم حساب SES الفعلي، وتظهر حالتها منفصلة عن مؤشرات الحملة.
6. لطلب Production Access استخدم **SES → Account dashboard → Request production access** في الـ Region نفسه، واختر **Marketing** لما يناسب استخدام القناة وقدّم وصف نموذج الموافقات وإلغاء الاشتراك. بعد قبول AWS أعد تشغيل Preflight واختبار الرسالة قبل الإرسال الفعلي.

البيئة الفعلية تستخدم إعدادات Compose من `.env.example` و`ERP_MARKETING_PUBLIC_URL` العام بـ HTTPS. اضبط الأسرار خارج السجلات وقاعدة البيانات، ومنها `ERP_MARKETING_HMAC_KEY` ثابت ومخصص للإنتاج. احفظ المفتاح ضمن إجراءات الاستعادة حتى تفضل بصمات الحظر قابلة للمطابقة. راجع الإعدادات قبل تشغيل `ERP_MARKETING_LIVE=1`، والقنوات الفعلية تُختبر بحساباتك أولًا.

لـ Twilio استخدم المرسل بصيغة E.164، وقالب Content SID معتمد لـ WhatsApp، وضبط `ERP_TWILIO_ACCOUNT_SID` و`ERP_TWILIO_AUTH_TOKEN`. يرسل الـ Worker رابط حالة الرسالة تلقائيًا، ولازم العنوان العام يطابق عنوان Webhook الفعلي لتنجح مطابقة التوقيع.

## الترحيلات والخصوصية

- `marketing/0001_initial` يضيف جداول الجمهور والموافقة والقوالب والحملات والتسليم والأتمتة والاستبيان.
- `marketing/0002_roles_and_locked_templates` يهيئ الأدوار ويضيف حماية PostgreSQL للقوالب المعتمدة.
- **Forget contact** يحتاج صلاحية أدمن وسببًا. يمسح الاسم والبريد/الهاتف ونسخ التسليم داخل Marketing، يبطل الرموز ويسجل الحظر بالبصمة ويستبعد التسليمات المعلقة. سجل الموافقة يحتفظ بالوقت والمصدر والبصمة. طلب إزالة السجل الأصلي من CRM أو من مزود البريد يُعالَج في النظام المالك.

### وثائق المزود

- [SES Domain identities وEasy DKIM](https://docs.aws.amazon.com/ses/latest/dg/creating-identities.html)
- [SES Custom MAIL FROM وSPF وMX](https://docs.aws.amazon.com/ses/latest/dg/mail-from.html)
- [SES DMARC alignment](https://docs.aws.amazon.com/ses/latest/dg/send-email-authentication-dmarc.html)
- [طلب SES Production Access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html)
- [توقيع Amazon SNS](https://docs.aws.amazon.com/sns/latest/dg/sns-verify-signature-of-message.html)
