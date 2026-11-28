> **مرجع الإصدار الحالي (`v2.11.0` — 2026-11-28):** هذا الدليل التخصصي معتمد ضمن حزمة `v2.11.0` (29 عقد موديول، 9 لغات واجهة وطباعة). لحالة التسليم ونقطة الفحص الحالية راجع `CHECKPOINT_V2_11_0.md` و`ERPi_Pro_Handoff_Docs/`.

# API لخدمات المنصة v2.6.0

الأساس `/api`. كل حساب عميل UUID مستقل عن Django User. المسارات الداخلية تظل Session-authenticated؛ لا تغيير لـ AUTH_USER_MODEL.

## خطوات العميل

1. `GET /api/public/v1/auth/csrf`: احفظ Cookie و`csrf_token`.
2. طلبات Register/Login/Refresh/Verify/Reset ترسل `X-CSRFToken` مع Cookie المقابلة. حماية CSRF تشمل Login نفسه.
3. `POST /api/public/v1/auth/register`:

```json
{"space":"main-store","display_name":"Ahmed","email":"ahmed@example.test","password":"Example-Strong-Password!739"}
```

التسجيل ليس Marketing Opt-in ولا يضيف موافقة إرسال تسويقي.

الإجابة 202 و`status=check_email`. لا توكن للحساب pending. كلمة المثال للتوثيق فقط، اختر كلمة مختلفة.

4. رابط Mailpit يفتح صفحة تأكيد. GET لا يعتمد الحساب؛ POST بزر Confirm هو الذي يستهلك الرابط.
5. `POST /api/public/v1/auth/login` مع `space,email,password` يعيد `access,expires_in,token_type`. Refresh Cookie باسم `erpi_customer_refresh`، HttpOnly/SameSite=Strict، Secure في الإنتاج، والمسار `/api/public/v1/auth/`.
6. `GET /api/customer/v1/me` مع `Authorization: Bearer <access>`.
7. `POST /api/public/v1/auth/refresh` مع `{}` وCookie/CSRF. صلاحية الجلسة 7 أيام من الدخول ولا تمتد كل مرة؛ Access مدته 5 دقائق.
8. `POST /api/public/v1/auth/logout` أو `/logout-all` مع Bearer. تلغى صلاحية Access فورًا عبر Session state في قاعدة البيانات.

قفل التدوير: Identity ثم Session داخل transaction. استهلاك التوكن نفسه مرتين، حتى لو جاء من طلبَي Refresh متزامنين، يسمح بنجاح واحد فقط ثم يلغي الجلسة عند اكتشاف إعادة الاستخدام. لا ترسل Refresh بالتوازي من عدة تبويبات؛ هذا قرار حماية وليس عملية idempotent تعيد نفس JWT.

## التحقق والاستعادة

- `POST /api/public/v1/auth/resend-verification`: `space,email`.
- `POST /api/public/v1/auth/verify-email`: `challenge_id,token`.
- `POST /api/public/v1/auth/password-reset/request`: `space,email`.
- `POST /api/public/v1/auth/password-reset/confirm`: `challenge_id,token,password`.

رسائل عامة لإخفاء وجود البريد. Challenge 24 ساعة للتأكيد وساعة للاستعادة، hash فقط محفوظ في جدول Challenge، وSMTP مؤجل عبر Outbox/Dispatcher/Celery. نجاح SMTP قد يتكرر عند انهيار العملية بعد الإرسال؛ الرابط نفسه يستهلك مرة واحدة. ثمانية محاولات إرسال كحد أقصى، مع backoff. عند إصلاح SMTP استخدم Resend من الواجهة لإنشاء تحدٍّ جديد إذا استُنفدت المحاولات أو انتهت الصلاحية.

## إدارة المواقع والحسابات — موظفون فقط

- `GET/POST /api/platform/v1/spaces`: إنشاء يحتاج `publication.manage` على site_id، وIdempotency-Key. الحقول: code,name,site_id,default_language.
- `GET/PATCH /api/platform/v1/spaces/{id}`: التعديل name,active,default_language + revision. الكود والموقع الداخلي ثابتان في هذا الإصدار.
- `GET /api/platform/v1/identities?space_id=...&page=1`: `public_identity.view` ضمن نطاق موقع الربط.
- `PATCH /api/platform/v1/identities/{uuid}`: status=active|disabled + revision؛ يحتاج `public_identity.manage` على All sites لأنه يؤثر على الهوية العالمية بكل عضوياتها. لا يمكن تفعيل حساب لم يؤكد البريد بعد.

الأدوار: Platform_Services_Admin موجود للتعيين من Access control، والموديولات الثلاثة تُفعّل صراحةً من Module Manager.

## الملفات

- `GET/POST /api/customer/v1/media`: Bearer، POST multipart باسم file وIdempotency-Key. المالك والموقع من الجلسة لا من جسم الطلب، والخصوصية private دائمًا.
- `GET /api/customer/v1/media/{uuid}` و`/download`: المالك نفسه وداخل مساحة الجلسة.
- `DELETE /api/customer/v1/media/{uuid}` مع revision: أرشفة منطقية.
- `GET/POST /api/platform/v1/media`: الموظف بصلاحية media_service.view/manage ونطاق Site، والإنشاء multipart مع space_id,file,visibility وIdempotency-Key.
- `GET /api/platform/v1/media/{uuid}` و`/download`: القراءة بصلاحية الموظف ونطاق الموقع.
- `GET /api/public/v1/media/{uuid}/download`: فقط ملف نشره موظف بصلاحية manage، visibility=public، وموقع عام فعال.

لا يعيد API object_key أو path. التنزيل Attachment مع nosniff وCSP sandbox. PDF يُتحقق من توقيعه ونهايته وحجمه، وهذا **ليس محرك فحص فيروسات**؛ لا توجد معاينة PDF داخلية في هذا الإصدار. الصور تُفحص وتُعاد كتابتها لإزالة metadata والبايتات الزائدة.

## القوائم والأخطاء

25 صفًا في الصفحة مع count,next,previous,results. ملفات العميل في My files تعرض أول صفحة في شاشة التجربة؛ الـ API يدعم بقية الصفحات.

Validation يعيد field_errors وfield_error_codes. الأخطاء العامة 401/403/404/409/429 لا تكشف أسرارًا. مسار عام غير مسموح لا يتحول إلى SPA fallback. لا يوجد CORS مفتوح؛ شاشة العميل تعمل على نفس Origin.

## ما هو عقد مستقبلي فقط

الروابط المالية Sales.Customer/Supplier، Search، PaymentProvider، Channels، Product/Post/Forum وSitemap ليست Endpoints منفذة في v2.6.0. لا تختبر مسارات المراحل التالية كأنها جزء من هذا التسليم.
