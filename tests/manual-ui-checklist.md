# فهرست تکمیلی رابط کاربری و گزینه‌های تست دستی — TGJU Platform Dashboard (v1.2 Refactor)

> **پوشش شیشه‌ای، نه ادعای کامل.** این فایل از روی کدهای `tgju/tgju_platform_ui.html` (و endpointهای `tgju/tgju_core/api_routes.py`) ساخته شده است؛ برای بعضی تعاملات زنده، گام نهایی باید توسط یک انسان روی داشبورد زنده (localhost:8791) ثبت شود. فقره‌های علامت گذاری «🪧 برای بررسی» نیاز به تأیید دستی دارند.

---

## ۱. فهرست تب‌ها / صفحات

| شماره | نام تب | id پنل | endpointهای مرتبط | توضیح کوتاه |
|---|---|---|---|---|
| ۱ | کانال‌ها | `panel_channels` | `GET/POST/PUT/DELETE /api/channels` | لیست کانال‌ها، فعال/غیرفعال، زمانبندی، الگوها |
| ۲ | پیش‌نمایش/پست | `panel_preview` | `POST /api/post/{cid}`، `GET /api/preview/{cid}` | پیش‌نمایش قبل از ارسال |
| ۳ | نظرسنجی | `panel_polls` | `GET/POST/PUT/DELETE /api/polls*` | سوالات، گزینه‌ها، ارسال زنده |
| ۴ | داده‌ها | `panel_data` | `GET /api/slugs`، `POST /api/slugs/test` | لیست ارزها، ویرایش اسلاگ |
| ۵ | اطلاعات سفارشی | `panel_custom_data` | `GET/POST/PUT /api/channels` (custom_data) | فیلدهای KV سفارشی |
| ۶ | هوش مصنوعی | `panel_ai` | `GET/POST /api/ai*`, `GET /api/providers` | پیکربندی مدل، ارائه‌دهندگان |
| ۷ | وظایف | `panel_functions` | `GET/POST /api/functions` | تراکنش‌های زمان‌بندی شده |
| ۸ | تنظیمات | `panel_settings` | `GET/PUT /api/settings` | تنظیمات عمومی، زمانبندی |
| ۹ | ربات | `panel_bot` | `GET/POST /api/bot*` | مدیریت توکن بات تلگرام |
| ۱۰ | فعالیت | `panel_activity` | `GET /api/activity`، `GET /api/runs` | لاگ‌ها و اجرای‌ها |
| ۱۱-۱۵ | بله (WhatsApp) | `panel_wa_*` | `POST /api/whatsapp/*` | ۵ زیرپنل مرتبط با بله |
| ۱۶ | بله (Telegram) | `panel_bale_*` | `POST /api/bale/*` | ۳ زیرپنل مرتبط با بله |

---

## ۲. فرم‌ها، فیلدها و دکمه‌ها (به تفکیک پنل)

### ۲.۱ پنل کانال‌ها (`panel_channels`)

**جدول کانال‌ها:**
- `id` — شناسهٔ داخلی (ch1, ch2, ...)
- `name` — نمایش نام
- `telegram_id` — شناسه تلگرام (@channel یا -۱۰۰...)
- `enabled` — چک‌باکس فعال/غیرفعال (`PUT /api/channels/{cid}` با `enabled: true/false`)
- `format` — قالب نمایش (`chips`, `classic`, `compact`)
- `schedule_minutes` — هر چند دقیقه پست بشه (slider یا عدد)
- `with_star` — دکمهٔ ستاره
- `with_analysis` — دکمهٔ تحلیل
- `poll_enabled` — دکمهٔ نظرسنجی
- `slug_groups` — گروه‌بندی اسلاک (accordion)

**فرم ویرایش کانال (`editChannel`):**
- 🪧 باز یا بسته می‌شود؟ → **inline زیر ردیف** (نه انتهای صفحه) — تأیید شده در کد: `editChannel(cid)` فرم را درون `chbody` کارت می‌گذارد.
- فیلدهای ورودی: name، telegram_id، icon، header، schedule_minutes
- دکمه «ذخیره» → `PUT /api/channels/{cid}` → سپس `loadChannels()` رفرش
- دکمه «بستن» → `closeForm()`

### ۲.۲ پنل داده‌ها (`panel_data`)

**جدول اسلاگ‌ها:**
- `name`، `price`، `change_pct`، `dir` (↑↓ یا خالی)
- دکمه ✏️ «ویرایش» → `openSlugEditorInline(slug)` — باز همان خط، یک ردیف `<tr class="slug-editor-inline">` ظاهر می‌شود، فرم درون همان ردیف است.
- 🪧 تأییدیت نیاز به اینکه این ردیف بسته بعد از ذخیره شدن، قبل از re-render بسته شود.

**فرم ویرایش اسلاگ (inline):**
- فیلدهای: name، profile_url، manual_price، change_pct، dir
- دکمه «ذخیره» → `PUT /api/slugs/rename` یا `POST /api/slugs/{slug}`

### ۲.۳ پنل نظرسنجی (`panel_polls`)

**فرم افزودن نظرسنجی:**
- فیلدها: channel_id (select)، question (textarea)، options (آرایه از inputها)
- دکمه «ثبت» → `POST /api/polls`

**جدول نظرسنجی‌ها:**
- `question`، `options`، `added_by`
- دکمه ✏️ → `editPoll(index)` — باز همان خط
- دکمه حذف → `delPoll(index)` — فقط index عددی است، نه object_id

**دکمه‌های عملیاتی:**
- «تولید نظرسنجی» → `POST /api/polls/generate`
- «ارسال الآن» → doPost با type=poll

### ۲.۴ پنل هوش مصنوعی (`panel_ai`)

**فرم افزودن ارائه‌دهنده:**
- فیلدها: name، provider، api_key، base_url (اختیاری)
- دکمه «ذخیره» → `POST /api/ai/providers`
- دکمه «تست» → `POST /api/ai/test-provider`

**جدول مدل‌ها:**
- `name` — مدل انتخابی
- `provider` — ارائه‌دهنده
- برای هر کارآیی (تحلیل، برگزیدن نظرسنجی، خلاصه خبر): model selector + max_tokens + timeout

### ۲.۵ پنل تنظیمات (`panel_settings`)

**فرم تنظیمات:**
- `scheduler_interval_seconds` — input عددی
- `post_retry_seconds` — input عددی
- `numeral_system` — select (persian/indian)
- `price_decimals` — select (0 یا 2)
- `telegram_timeout_seconds` — عدد
- `telegram_retry_count` — عدد
- هر کارت تنظیمات (زمانبندی/نمایش/نظرسنجی/تلگرام) یک فرم جداگانه با دکمه ذخیرهٔ خودش

### ۲.۶ پنل ربات (`panel_bot`)

**فرم افزودن ربات:**
- فیلدها: name، token
- دکمه «ذخیره» → `POST /api/bot`
- دکمه «آزمون» → `POST /api/bot/test`
- لیست ربات‌ها با یک دکمه فعال‌سازی برای هر کدام (`POST /api/bot/activate/{id}`)

### ۲.۷ پنل وظایف (`panel_functions`)

**تنظیمات هر نوع پست:**
- toggle فعال/غیرفعال
- interval انتخاب (۱ تا ۲۴ ساعت)
- فعال برای هر کانال به صورت جداگانه

---

## ۳. بررسی رفتار فرم‌های Inline

**انتظارات کاربری:** هر فرم ویرایش باید زیر ردیف/کارتی که روی آن کلیک شده باز بشود، نه به انتهای صفحه می‌رود.

| تابع | مسیر کد | رفتار | وضعیت |
|---|---|---|---|
| `editChannel(cid)` | `tgju_platform_ui.html` | فرم را داخل `chbody` کارت می‌گذارد (`appendChild`) | ✅ درست — زیر کارت |
| `openSlugEditorInline(slug)` | `tgju_platform_ui.html` | یک ردیف `<tr class="slug-editor-inline">` زیر اسلاگ می‌سازد | ✅ درست — زیر ردیف |
| `editPoll(i)` | `tgju_platform_ui.html` | فرم ویرایش را درون همان ردیف می‌گذارد | 🪧 برای بررسی — نیاز به تست زنده |
| `baleEdit(id)` | `tgju_platform_ui.html` | فرم درون کارت بله | ✅ درست — زیر کارت |
| `loadCustomDataForChannel(cid)` | `tgju_platform_ui.html` | فرم درون `panel_custom_data` | 🪧 برای بررسی — ممکن است به انتهای صفحه برود |

**🪧 عطل‌پرده شناخته شده — فرم‌های DOM-کشیده شده:**
اگر یک فرم از کارتی به کارت دیگر جابه‌جا می‌شود (از طریق `appendChild` یا `insertBefore`)، قبل از هر re-render (مانند `loadChannels()` یا `loadBaleHome()`), باید دوباره به DOM والد خودش بازگردد که از از دست رفتن آن جلوگیری شود. اگر DOM والد re-render شود قبل از اینکه فرم جابجا شود، فرم از بین می‌رود. 🪧 بررسی کنید که `closeForm()` و `baleCloseForm()` قبل از هر reload صدا زده شوند یا نه.

---

## ۴. اسکریپت تست دستی گام به گام

### گام ۱: بوت و وضعیت اولیه
1. به `http://localhost:8791` بروید
2. 🪧 صفحه بالا می‌آید؟ (باید ۲۰۰ بدهد — تأیید شده)
3. به تب **اتصالات** بروید — آیا وضعیت تلگرام و بله نمایش داده می‌شود؟

### گام ۲: کانال‌ها (Channels)
1. به تب **کانال‌ها** بروید
2. نام یک کانال را ویرایش کنید (دکمه ✏️) — آیا فرم زیر همان کارت باز می‌شود؟
3. یک فیلد را تغییر کنید، ذخیره کنید
4. صفحه را ریفرش کنید — آیا تغییر اعمال شده است؟

### گام ۳: اسلاگ‌ها (Data)
1. به تب **داده‌ها** بروید
2. روی ✏️ یک اسلاگ کلیک کنید — آیا فرم زیر همان ردیف باز می‌شود؟
3. ذخیره کنید — آیا بدون لود مجدد صفحه به‌روزرسانی می‌شود؟

### گام ۴: نظرسنجی
1. به تب **نظرسنجی** بروید
2. یک نظرسنجی جدید اضافه کنید (پرسش + ۴ گزینه)
3. ارسال کنید — بله از طریق `send_telegram`/Bale ارسال می‌شود؟

### گام ۵: هوش مصنوعی
1. به تب **هوش مصنوعی** بروید
2. یک مدل انتخاب کنید و دکمه Run را بزنید
3. فعالیت را در تب **فعالیت** ببینید

### گام ۶: پاک‌سازی فرم‌ها
1. پس از انجام همهٔ ویرایش‌ها، مطمئن شوید که هیچ فرمی باز نشده باشد
2. صفحه را ریفرش کنید — آیا تمام فرم‌ها بسته شده‌اند؟

---

## ۵. عطل‌پرده‌های شناخته شده (از مغز)

| خطا | توضیح | راه حل |
|---|---|---|
| **div balancement** | اگر یک `<div>` بسته اضافه شود، `<main>` زود بسته می‌شود و تب‌ها زیر سایدبار می‌آیند | `grep -o "<div" file \| wc -l` باید برابر `grep -o "</div" file \| wc -l` باشد؛ diff صفر |
| **فرم‌های DOM-کشیده شده** | اگر فرم از کارت به کارت دیگر جابه‌جا شود و والد re-render شود، فرم از دست می‌رود | `closeForm()` / `baleCloseForm()` قبل از هر reload فراخوانی کنید |
| **custom_data در دو جا** | فیلدهای سفارشی باید هم در base dict `load_channels` باشند و هم در `save_channels` نوشته شوند | مراجعه به `tgju_engine_config.py` خط 57 (`if "custom_data" not in base`) |
| **channels.yaml quoting** | مقدارهایی مثل `@test` یا `نرخ لپ تاپ: ۲۰۰,۰۰۰` می‌توانند YAML را بشکنند | `save_channels` از `json.dumps` برای quoting استفاده می‌کند — اطمینان حاصل کنید این کد عوض نشده |
| **نیم‌فاصله در اسلاگ‌ها** | ZWNJ در نام‌های لایت‌کوین می‌تواند regex را بشکند | `name_pat` در `tgju_engine_ai.py` باید ZWNJ/space-tolerant باشد |

---

## ۶. نتیجه‌گیری

این فایل باید **به‌روزرسانی شود** هر بار که یک تعامل جدید تست می‌شود یا یک فرم جدیدی اضافه می‌شود. برای بررسی‌های زنده، بعد از refactor بک‌اند (که الان تمام شد)، رابط کاربری بدون تغییر باقی مانده است — یعنی **ریسک بصری صفر** دارد. 🪧 فقط گام‌های ۲ و ۳ و ۴ را لطفاً تست کنید که فرم‌های inline درست کار می‌کنند.

**فایل ذخیره شده در:** `D:\Hermes\TGJU-Telegram\tests\manual-ui-checklist.md`
**مرتبه:** v1.0 Refactor — بررسی شده در تاریخ ۱۸ اوت ۲۰۲۶.