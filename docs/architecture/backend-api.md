# بک‌اند، API و قرارداد اتصال

وضعیت: پیشنهاد قرارداد؛ فایل OpenAPI کامل پس از پذیرش تصمیم‌ها تولید می‌شود.

## مرزهای کد

ساختار هدف پیشنهادی در صورت پذیرش Next.js؛ اگر قالب Django پذیرفته شود، web در backend/templates و assets قرار می‌گیرد و apps/web runtime مستقل نخواهد داشت:

~~~text
apps/backend/
  config/
  modules/identity/
  modules/organizations/
  modules/catalog/
  modules/cinema/
  modules/inventory/
  modules/orders/
  modules/payments/
  modules/ticketing/
  modules/finance/
  modules/integrations/
  modules/notifications/
  modules/support/
  modules/audit/
  modules/operations/
  modules/site_settings/
apps/web/
packages/api-client/
contracts/
infra/
docs/
reference/
~~~

این ساختار هنوز ساخته یا جایگزین کد فعلی نشده است. در مرحلهٔ انتقال، نمونهٔ فعلی با تاریخچه و آزمون‌ها حفظ می‌شود؛ حذف ناگهانی آن جزو این پیشنهاد نیست.

API view ورودی را اعتبارسنجی و هویت را استخراج می‌کند؛ سرویس use case مجوز، قواعد و مرز تراکنش را کنترل می‌کند. ORM مسئول ذخیره است؛ adapter مسئول تماس بیرونی. side effectهای مالی در signalهای نامرئی پراکنده نمی‌شوند. نوشتن مستقیم جدول ماژول دیگر خارج از سرویس مالک مجاز نیست.

Django Admin برای تنظیمات و پشتیبانی محدود است؛ ثبت مالی قطعی، تغییر دستی وضعیت فروش و پذیرش بلیط با ویرایش آزاد model انجام نمی‌شود.

## قرارداد عمومی

- پیشوند /api/v1؛ زمان‌ها ISO 8601 با UTC و مبلغ عدد صحیح ریال.
- پاسخ خطا دارای code، message، request_id و خطای فیلد است؛ هیچ stack trace یا کلید سرویس بیرونی به مرورگر نمی‌رود.
- Idempotency-Key برای رزرو، تمدید، آزادسازی، claim مهمان، quote دارای تخصیص، اعمال تخفیف، سفارش، شروع پرداخت، redeem، لغو و فرمان‌های مالی لازم است. scope و سیاست ذخیرهٔ خطا مطابق قرارداد state-transitions است.
- 401 برای نشست لازم، 403 برای مجوز ناکافی، 409 برای تعارض ظرفیت یا کلید، 422 یا 400 برای ورودی نامعتبر با قاعدهٔ ثابت در OpenAPI، و 503 برای نبود دسترسی معتبر به مرجع.
- نتیجهٔ درحال‌پردازش با شناسهٔ عملیات و مسیر پیگیری بازمی‌گردد؛ 202 به معنی فروش موفق نیست.
- pagination، سقف پاسخ و scope داده برای همهٔ فهرست‌ها تعیین می‌شود.
- شیوهٔ 400 یا 422 در قرارداد نهایی فقط یکی انتخاب می‌شود؛ این سند هنوز OpenAPI نهایی نیست.

## مسیرهای اصلی پیشنهادی

| حوزه | فرمان‌ها یا مسیرها | شرط مهم |
| --- | --- | --- |
| هویت | POST auth/otp/request، auth/otp/verify، auth/logout؛ GET auth/me | ضد سوءاستفاده و چرخش نشست |
| عمومی | GET cinemas، films، screenings و جزئیات | فقط محتوای منتشرشده |
| موجودی | GET screenings/{id}/inventory?since_version= | بدون اطلاعات مالک رزرو |
| رزرو | POST holds؛ POST holds/{id}/extend یا release | مالکیت از سرور؛ پاسخ دارای server_time و expires_at |
| مهمان | POST holds/{id}/claim | اثبات نشست مهمان و ورود؛ انتقال اتمیک مالکیت و بازسنجی سقف |
| قیمت | POST quotes؛ POST orders/{id}/discounts | quote نسخه‌دار؛ مبلغ و سهمیه از backend |
| سفارش | POST orders؛ GET orders/{id} | قیمت از backend، فقط خریدار یا مسئول مجاز |
| پرداخت | POST orders/{id}/payment-attempts | مبلغ از snapshot، URL درگاه allowlist |
| درگاه | GET/POST providers/{provider}/callback | ورودی غیرقابل اعتماد، سپس استعلام provider |
| بلیط | GET orders/{id}/tickets | بعد از فروش و پرداخت قطعی |
| ورودی | POST admissions/validate و admissions/redeem | validate مصرف نیست؛ redeem تراکنشی و دارای مجوز محل |
| لغو | POST cancellations و GET cancellations/{id} | بررسی قواعد و وضعیت مصرف |
| مالی | GET centers/{id}/statement؛ POST settlements/prepare، approve، start-transfer، record-payment | مجوز مستقل و کنترل هم‌زمانی مانده |
| بازپرداخت | POST refunds/{id}/approve، start-transfer و record-payment | تأیید درخواست با پرداخت یکی نیست |
| عملیات | GET review-cases؛ POST assign، action و escalate | مسئول، موعد و سابقهٔ تغییر |
| مرکز | مدیریت برنامه و قیمت از /center/... | scope مرکز و تأیید انتشار |
| اپراتور | مدیریت تنظیمات و گزارش از /operator/... | مجوزهای جدا و audit |

## نشست و امنیت

وب و API زیر یک origin از Nginx سرو می‌شوند. session در PostgreSQL، کوکی HttpOnly و Secure، SameSite مناسب و CSRF صریح برای تمام فرمان‌های مرورگر، از جمله ورود، استفاده می‌شود. دامنهٔ کوکی به میزبان محدود است. JWT در localStorage مسیر پایه نیست.

در callback درگاه به کوکی مرورگر برای یافتن سفارش اتکا نمی‌شود؛ state تصادفی، شناسهٔ تراکنش و استعلام معتبر استفاده می‌شوند. استثنای CSRF فقط برای endpoint ماشینی لازم و همراه احراز اصالت provider است، نه کل API.

اپراتور و کارکنان به مجوز سرویس و scope مرکز نیاز دارند؛ نقش از بدنهٔ درخواست پذیرفته نمی‌شود. OTP با TTL، hash، سقف تلاش، مصرف یک‌بار، محدودیت شماره و شبکه و نشست کنترل می‌شود. مجوز مالی حساس نیازمند تأیید هویت مجدد و ثبت دلیل است؛ روش دقیق MFA پیش از عرضهٔ پنل مدیریتی انتخاب می‌شود.

[رفتار CSRF در Django](https://docs.djangoproject.com/en/5.2/ref/csrf/)

## درگاه و مرجع موجودی

رابط PaymentProvider: create_attempt، verify، query و در صورت پشتیبانی refund/query_refund. نتیجه شامل شناسهٔ provider، مبلغ، ارز و قطعیت است. موفقیت browser callback اثبات دریافت وجه نیست.

رابط InventoryProvider: capabilities، snapshot، reserve، extend، confirm، release، cancel_sale و query_operation. هر adapter محدودهٔ قابلیت‌ها، زمان‌ها و رفتار عدم قطعیت دارد. نبود extend با تمدید محلی جبران نمی‌شود.

رابط TicketIssuer و AdmissionProvider برای بلیط بیرونی لازم است؛ شریک موجودی الزاماً بلیط ما را معتبر نمی‌داند. مالک مالی، صادرکننده و مسئول بازپرداخت برای هر کانال ثبت می‌شوند.

به retry خودکار عملیات تغییردهنده فقط با کلید تک‌اثری معتبر اجازه داده می‌شود. شبکه بیرونی داخل تراکنش قفل‌دار فراخوانی نمی‌شود.

## زیرساخت صف در حال بازبینی

A06 اکنون میان صف PostgreSQL و Celery/RabbitMQ در حال ارزیابی است. [مقایسهٔ گزینه‌ها](decision-options.md) مرجع این بازبینی است؛ شرح broker در ادامه فقط برای گزینهٔ RabbitMQ است. A08 نیاز به Inbox/Outbox ماندگار را مستقل از انتخاب broker حفظ می‌کند.

## Outbox، صف و تکرار

تغییر دامنه و OutboxEvent در یک commit ثبت می‌شوند. ناشر با lease و تلاش مجدد، پیام را به RabbitMQ می‌فرستد؛ نتیجهٔ انتشار ثبت می‌شود. گم‌شدن تأیید می‌تواند ارسال دوباره بسازد. مصرف‌کننده با ConsumerInbox و قید یکتا نتیجه را یک‌بار اعمال می‌کند.

on_commit به‌تنهایی صف ماندگار نیست؛ crash پس از commit و پیش از ارسال باید از Outbox بازیابی شود. taskها کوتاه، تک‌اثری و با timeout و backoff هستند. زمان‌های دور در ScheduledJob دیتابیس نگهداری و نزدیک موعد به صف وارد می‌شوند؛ انقضای رزرو به تحویل به‌موقع یک task وابسته نیست.

publisher confirm و consumer ack فعال می‌شوند. خطای دائمی به صف بررسی و هشدار می‌رود؛ retry بی‌پایان بدون مسئول مجاز نیست. صف اعلان از کار مالی و موجودی اولویت پایین‌تری دارد. قطعی پیامک خرید را برنمی‌گرداند.

[Celery و RabbitMQ](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/rabbitmq.html)؛ [قابلیت اطمینان RabbitMQ](https://www.rabbitmq.com/docs/reliability)

## قرارداد کانال بیرونی

کلید هر کانال، scopes، شناسهٔ کلید، timestamp، nonce و امضای payload تعریف می‌شوند؛ ارتباط TLS اجباری است. رخداد نسخه، شناسهٔ یکتا، aggregate و sequence دارد. شمارهٔ ترتیب در دامنهٔ مشخص معنا دارد؛ نباید فرض شود شمارهٔ auto-increment دیتابیس ترتیب commit همهٔ تراکنش‌هاست. sequence هر موجودی زیر قفل همان aggregate تخصیص می‌یابد.

دریافت‌کننده تکرار و جاافتادگی را مدیریت و snapshot کامل را قابل دریافت دارد. retention و شیوهٔ ادامه پس از خروج از بازه مشخص می‌شوند. امضای webhook، تعویض کلید، آزمون دسترسی متقاطع و sandbox قبل از اتصال واقعی الزامی‌اند.

## خروجی‌های لازم پیش از ساخت رابط کامل

OpenAPI نسخه‌دار، مدل خطا، جدول وضعیت و فرمان، matrix مجوز، قرارداد provider و دادهٔ آزمایشی باید تولید شوند. TypeScript client از schema تولید و تغییر ناسازگار در CI آشکار شود. client تولیدشده مبلغ یا مجوز را معتبر نمی‌کند؛ backend همیشه مرجع است.

## فرض‌های provider که باید تأیید شوند

هیچ قاعدهٔ عمومی دربارهٔ مهلت verify یا برگشت خودکار درگاه‌های ایرانی در کد فرض نمی‌شود. برای provider انتخابی، مهلت create/verify، معنای query، تفاوت authorized/verified/settled، برگشت خودکار و شناسهٔ بازگشت باید با سند همان provider و sandbox ثبت شود. T47 این رفتار را می‌آزماید. پاسخ «برگشت خورده» باید با دفتر دریافت و بازپرداخت تطبیق داده شود تا وجه دوباره برنگردد.

## قراردادهای مرجع

[فرمان‌ها و وضعیت‌ها](../contracts/state-transitions.md) و [مجوزها](../contracts/permissions.md) به API مقدم‌اند. ApproveRefund، ClaimGuestHold، QuoteOrder، ApplyDiscount، RedeemTicket و StartTransfer باید در OpenAPI نهایی قابل ردیابی باشند؛ بودن متن صفحهٔ frontend جای قرارداد را نمی‌گیرد.
