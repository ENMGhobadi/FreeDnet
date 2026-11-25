# FreeDnet

تونل عبور از فیلترینگ با معماری سه‌لایه و مدیریت هوشمند زنجیره اتصال، قابل دیپلوی مستقیم از گیت‌هاب روی **Render**، **SnappDeploy** و **Oracle**، همراه با رله **Google Apps Script** و کلاینت گرافیکی **ویندوز ۱۱**.

```text
                       [حالت اول: فیلترینگ شدید (Via GAS)]
[V2Ray / Browser] ---> [127.0.0.1:1080] ---> [GAS Account 1..N (تقسیم سهمیه)] ---> [Backend Server 1..M] ---> [Internet]

                       [حالت دوم: اتصال مستقیم و سریع (Direct)]
[V2Ray / Browser] ---> [127.0.0.1:1080] ------------------------------------------> [Backend Server 1..M] ---> [Internet]
```

---

## امکانات کلیدی
- **پروکسی محلی هم‌زمان SOCKS5 و HTTP** روی `127.0.0.1:1080` (مناسب برای V2Ray / v2rayN و مرورگر).
- **انتخاب زنده نوع زنجیره (`Chain Mode`):** جابه‌جایی آنی بین حالت عبور از **Google Apps Script** و حالت **Direct** (مستقیم به سرور مقصد بدون مصرف سهمیه گوگل).
- **تقسیم هوشمند سهمیه بین `1` تا `N` اکانت گوگل اسکریپت:** تراز خودکار مصرف روزانه، محدودیت ۲۰ اتصال هم‌زمان (زیر سقف ۳۰ تایی گوگل)، کنترل نرخ دقیقه‌ای، Cooldown خودکار و Long-Polling تطبیقی برای اتصال چندساعته.
- **پشتیبانی از چند سرور مقصد + Auto-Failover:** امکان افزودن چند آدرس Render / SnappDeploy / Oracle و سوئیچ خودکار به سرور سالم بعدی در صورت قطعی یا اتمام سهمیه.

---

## راهنمای کامل راه‌اندازی (قدم‌به‌قدم تا مرحله آخر)

### مرحله ۱: راه‌اندازی سرور مقصد (Render / SnappDeploy / Oracle)

می‌توانید یک یا چند سرور بسازید تا در صورت از کار افتادن یکی، برنامه به‌صورت خودکار روی دیگری سوئیچ کند.

#### روش الف) دیپلوی رایگان روی Render (مطابق پنل جدید Render)
1. وارد [داشبورد Render](https://dashboard.render.com/) شوید و ثبت‌نام یا ورود کنید.
2. از بالا روی **+ New** کلیک کنید و گزینه **Web Service** را انتخاب کنید.
3. در بخش **Source Code** مخزن گیت‌هاب خود (`ENMGhobadi / FreeDnet`) را انتخاب کنید.
4. فیلدهای فرم را دقیقاً مطابق زیر پر کنید:
   - **Name:** یک نام یکتا بنویسید (مثلاً `FreeDnet`).
   - **Language:** گزینه **Docker** را انتخاب کنید (ساده‌ترین حالت؛ نیازی به وارد کردن Build/Start Command نیست).
     *(اگر **Node** را انتخاب کردید، در کادر اول `npm install` و در کادر دوم `node server.js` بنویسید؛ مقدار پیش‌فرض `yarn install` / `yarn start` هم کار می‌کند.)*
   - **Branch:** `main`
   - **Region:** یک ریجن انتخاب کنید (مثلاً `Ohio (US East)` یا `Frankfurt (EU Central)`).
   - **Root Directory (Optional):** **خالی بگذارید** (چیزی ننویسید).
   - **Compute:** پلن رایگان **`$0 / month`** (`0.1 CPU`, `512 MB RAM`) را انتخاب کنید.
5. کمی پایین‌تر در بخش **Environment Variables** روی **Add Environment Variable** بزنید:
   - **Key:** `AUTH_KEY`
   - **Value:** یک رمز امن و دلخواه بنویسید (مثلاً `MySecretKey2026`) — **این رمز را یادداشت کنید.**
6. در پایین صفحه روی دکمه سفید **Deploy web service** کلیک کنید و ۱ تا ۲ دقیقه صبر کنید تا وضعیت **Live** شود.
7. لینک بالای صفحه (مثلاً `https://freednet.onrender.com`) آدرس سرور شماست.

#### روش ب) دیپلوی رایگان روی SnappDeploy (مطابق پنل SnappDeploy)
1. وارد پنل **SnappDeploy** شوید و در داشبورد روی **Connect GitHub** (یا دکمه **+ Deploy** و سپس **Deploy from GitHub →**) کلیک کنید.
2. در پنجره **Deploy Your Application** موارد زیر را انتخاب کنید:
   - **Select Repository:** `ENMGhobadi/FreeDnet`
   - **Deploy Branch:** `main`
   - **Container Name:** یک نام دلخواه (مثلاً `freednet`) — آدرس نهایی شما زیر همین کادر نمایش داده می‌شود (مثلاً `https://freednet-xxxx.containers.snapdeploy.app`).
   - **Compute Type:** `CPU` (`Standard compute - Free with auto-sleep`)
   - **Container Size:** `Small` (`512 MB / 0.25 vCPU` — رایگان)
   - تیک **Auto Deploy on Push** را روشن بگذارید و روی **Deploy from GitHub** کلیک کنید.
3. در پنجره **Port Configuration**:
   - پورت **3000** به‌صورت خودکار از `Dockerfile` شناسایی شده است؛ روی **✓ Continue** کلیک کنید.
4. در پنجره **Environment Variables Detected** مقادیر زیر را وارد کنید:
   - **AUTH_KEY** *(Required)*: رمز امن و دلخواه خود (مثلاً `MySecretKey2026`).
   - **SESSION_IDLE_MS** *(Required)*: عدد **`300000`** را وارد کنید (معادل ۵ دقیقه برای پاکسازی نشست‌های بیکار).
   - **NODE_ENV:** مقدار `production` را بگذارید.
   - **PORT = 3000:** از `Dockerfile` خوانده شده و نیازی به Override ندارد.
5. روی دکمه **Deploy** کلیک کنید و صبر کنید تا کانتینر آماده شود.

#### روش ج) دیپلوی روی سرور شخصی (Oracle / VPS با Docker)
```bash
git clone https://github.com/ENMGhobadi/FreeDnet.git
cd FreeDnet
docker build -t freednet .
docker run -d --name freednet -p 3000:3000 -e AUTH_KEY="MySecretKey2026" --restart unless-stopped freednet
```

---

### مرحله ۲: راه‌اندازی Google Apps Script (برای حالت فیلترینگ شدید)

اگر همیشه در حالت **Direct** متصل می‌شوید این مرحله اختیاری است، اما برای عبور از فیلترینگ شدید حداقل ۱ اکانت (و برای سرعت و سهمیه بیشتر چند اکانت گوگل) بسازید:

1. وارد [Google Apps Script](https://script.google.com/) شوید و با اکانت گوگل خود لاگین کنید.
2. روی **New project** کلیک کنید.
3. کد پیش‌فرض داخل ادیتور (`Code.gs`) را کامل پاک کنید و تمام محتوای فایل [`gas/Code.gs`](https://github.com/ENMGhobadi/FreeDnet/blob/main/gas/Code.gs) را داخل آن قرار دهید.
4. دو خط بالای کد را مطابق مرحله ۱ ویرایش کنید:
   ```javascript
   const DEFAULT_SERVER_URL = "https://freednet.onrender.com/tunnel";
   const AUTH_KEY = "MySecretKey2026";
   ```
   *(دقت کنید که `/tunnel` در انتهای آدرس سرور باشد و `AUTH_KEY` دقیقاً با رمز مرحله ۱ یکی باشد.)*
5. آیکون ذخیره (Save) را بزنید، سپس از بالا سمت راست روی **Deploy** و بعد **New deployment** کلیک کنید.
6. کنار عبارت **Select type** روی آیکون چرخ‌دنده کلیک کرده و **Web app** را انتخاب کنید.
7. گزینه‌ها را دقیقاً به این صورت تنظیم کنید:
   - **Description:** `FreeDnet`
   - **Execute as:** `Me`
   - **Who has access:** `Anyone`
8. روی **Deploy** کلیک کنید.
9. در پنجره **Authorize access**:
   - اکانت گوگل خود را انتخاب کنید.
   - اگر صفحه هشدار باز شد، روی **Advanced** و سپس روی **Go to ... (unsafe)** کلیک کنید و در نهایت **Allow** را بزنید.
10. لینک **Web app URL** که انتهای آن `/exec` دارد را کپی و ذخیره کنید.
11. *(پیشنهادی)* برای افزایش ظرفیت روزانه و سرعت، همین مراحل را روی ۲ تا ۱۰ اکانت جیمیل دیگر هم تکرار کنید.

---

### مرحله ۳: نصب و اجرای کلاینت ویندوز ۱۱

1. مطمئن شوید **Python 3.8 به بالا** روی ویندوز ۱۱ نصب است. در صورت نیاز از [صفحه دانلود پایتون](https://www.python.org/downloads/) نصب کنید (**حتماً در صفحه اول نصب تیک `Add python.exe to PATH` را بزنید**).
2. مخزن [FreeDnet](https://github.com/ENMGhobadi/FreeDnet) را دانلود (Code -> Download ZIP) و از حالت فشرده خارج کنید.
3. وارد پوشه `client` شوید و روی فایل `run.bat` دبل‌کلیک کنید تا برنامه گرافیکی باز شود.
4. در بخش بالای تنظیمات (**Connection, Chain Mode & Quota Protection**):
   - **Auth Key:** همان رمز `AUTH_KEY` مرحله ۱ را وارد کنید.
   - **Listen IP:** `127.0.0.1` و **Port:** `1080` (پیش‌فرض).
   - **Chain Mode:** بسته به شدت فیلترینگ، یکی از دو حالت `Via Google Apps Script` یا `Direct to Backend Servers` را انتخاب کنید.
5. در تب **Google Apps Script Accounts**:
   - روی **+ Add GAS Account** بزنید، یک نام دلخواه (مثلاً `GAS 1`) و لینک `/exec` مرحله ۲ را وارد کنید. بقیه اکانت‌ها را هم اضافه کنید.
6. در تب **Backend Servers (Render / SnappDeploy / Oracle)**:
   - روی **+ Add Server** بزنید و آدرس سرورهای خود (مثلاً `https://freednet.onrender.com` یا `https://freednet-xxxx.containers.snapdeploy.app`) را اضافه کنید. این کار هم برای حالت **Direct** و هم برای سوئیچ خودکار (**Auto-Failover**) در حالت **GAS** استفاده می‌شود.
7. از بالای پنجره روی **Ping All** بزنید و در کادر **Activity Log** مطمئن شوید وضعیت اکانت‌ها و سرورها `OK` است.
8. روی دکمه آبی **Start Proxy** کلیک کنید تا وضعیت به `Running` تغییر کند.
9. روی **Test Proxy (E2E)** بزنید؛ اگر پیام `E2E Proxy Test OK ... (HTTP/1.1 204 No Content)` را دیدید، تونل شما کاملاً آماده است!

---

### مرحله ۴: اتصال V2Ray / v2rayN یا مرورگر به `127.0.0.1:1080`

#### در v2rayN (ویندوز)
1. برنامه **v2rayN** را باز کنید.
2. از منوی بالا روی **Servers** کلیک کرده و **Add [SOCKS] server** را بزنید.
3. مقادیر زیر را وارد کنید:
   - **Address:** `127.0.0.1`
   - **Port:** `1080`
   - **Remarks:** `FreeDnet`
4. این سرور را انتخاب و با `Enter` فعال کنید و در پایین برنامه گزینه **Set system proxy** را روشن کنید.

#### استفاده به‌عنوان Outbound در هسته V2Ray / Xray
اگر می‌خواهید کانفیگ V2Ray خودتان را از داخل FreeDnet عبور دهید، در `outbounds` یک خروجی SOCKS روی `127.0.0.1:1080` تعریف کنید یا در مرورگر/تلگرام پروکسی **SOCKS5** با آدرس `127.0.0.1` و پورت `1080` تنظیم کنید.

> **نکته درباره تست پینگ:** دستور `ping` معمولی در CMD ویندوز (پروتکل ICMP) از پروکسی‌های SOCKS5 و HTTP عبور نمی‌کند. برای سنجش تاخیر واقعی اتصال، از دکمه **Test Proxy (E2E)** در خود برنامه یا تست **Real Delay / TCPing** در v2rayN استفاده کنید.
