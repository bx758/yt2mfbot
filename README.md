# yt2mf — Google Cloud Workers

نسخه نهایی معماری Google Cloud برای `yt2mf`.

## معماری

```text
Telegram
   │
   ▼
┌──────────────────────┐
│ Main GCE VM          │
│ app.main             │
│ - Telegram updates   │
│ - metadata only      │
│ - PostgreSQL writes  │
│ - Pub/Sub publisher  │
└──────────┬───────────┘
           │ job_id
           ▼
┌──────────────────────┐
│ Google Pub/Sub       │
│ video-jobs           │
└──────────┬───────────┘
           │
           ▼
┌────────────────────────────────┐
│ Managed Instance Group         │
│ autoscaling workers            │
│                                │
│ app.worker                     │
│  ├─ claim PostgreSQL lease     │
│  ├─ yt-dlp / Deno              │
│  ├─ ffmpeg                     │
│  └─ Telegram / MediaFire       │
└────────────────────────────────┘
           │
           ▼
┌──────────────────────┐
│ Cloud SQL PostgreSQL │
└──────────────────────┘
```

اصل مهم: **Main هیچ دانلود، compression یا upload انجام نمی‌دهد.** فقط metadata را می‌گیرد، Job را در PostgreSQL می‌سازد و `job_id` را به Pub/Sub می‌فرستد.

Worker بعد از دریافت پیام، Job را با PostgreSQL row lock و lease اختصاص می‌دهد. بنابراین حتی اگر چند VM همزمان همان Pub/Sub message را ببینند، فقط یک Worker مالک Job می‌شود.

## ساختار

```text
yt2mf-google-cloud-workers/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   ├── downloader.py
│   ├── compressor.py
│   ├── job_manager.py
│   ├── main.py
│   ├── pubsub.py
│   ├── uploader.py
│   ├── utils.py
│   └── worker.py
├── mfcmd.py
├── requirements.txt
├── README.md
├── systemd/
│   ├── yt2mf-main.service
│   └── yt2mf-worker.service
└── terraform/
    ├── main.tf
    ├── variables.tf
    ├── startup-main.sh
    └── startup-worker.sh
```

## پیش‌نیاز

- Google Cloud project
- Billing فعال
- یک Git repository عمومی که این پروژه داخل آن باشد
- Telegram Bot Token
- MediaFire session JSON
- در صورت نیاز YouTube cookies
- `gcloud`
- `terraform >= 1.6`

## 1. فعال کردن Google Cloud

```bash
gcloud auth login
gcloud auth application-default login

gcloud config set project YOUR_PROJECT_ID
```

## 2. Terraform

```bash
cd terraform
terraform init
```

یک `terraform.tfvars` بساز:

```hcl
project_id       = "YOUR_PROJECT_ID"
region           = "europe-west1"
zone             = "europe-west1-b"
repo_url         = "https://github.com/YOUR_USER/YOUR_REPO.git"

machine_type_main   = "e2-standard-2"
machine_type_worker = "c3-standard-4"

worker_min = 0
worker_max = 10

db_password = "CHANGE_ME_LONG_RANDOM_PASSWORD"
bot_token   = "123456:CHANGE_ME"
```

سپس:

```bash
terraform plan
terraform apply
```

### نکته مهم درباره Secretها

`db_password` و `bot_token` در Terraform state قرار می‌گیرند. برای production از remote state امن و encryption استفاده کن و state را داخل Git قرار نده.

## 3. MediaFire session و YouTube cookies

Workerهای جدید با startup script این دو فایل را از GCS می‌گیرند:

```text
gs://YOUR_BUCKET/session.json
gs://YOUR_BUCKET/youtube-cookies.txt
```

Bucket را از خروجی Terraform بگیر:

```bash
terraform output -raw assets_bucket
```

سپس:

```bash
gcloud storage cp session.json gs://YOUR_BUCKET/session.json
gcloud storage cp youtube-cookies.txt gs://YOUR_BUCKET/youtube-cookies.txt
```

وجود cookies اختیاری است.

## 4. بررسی Main

IP را بگیر:

```bash
terraform output -raw main_ip
```

روی Main:

```bash
ssh YOUR_USER@MAIN_IP
sudo systemctl status yt2mf-main --no-pager
sudo journalctl -u yt2mf-main -f
```

## 5. بررسی Workerها

```bash
gcloud compute instance-groups managed list --regions=europe-west1
```

و روی Worker:

```bash
sudo systemctl status yt2mf-worker --no-pager
sudo journalctl -u yt2mf-worker -f
sudo systemctl status yt2mf-cloud-sql-proxy --no-pager
```

## 6. Autoscaling

Autoscaler بر اساس تعداد پیام‌های تحویل‌نشده subscription کار می‌کند:

```text
0 jobs     -> 0 workers
1+ jobs    -> worker(s)
backlog ↑  -> workers ↑
backlog ↓  -> workers ↓
```

برای حفظ سرعت، `worker_min = 1` تنظیم کن. برای کمترین هزینه، `worker_min = 0` مناسب است ولی startup یک Worker چند دقیقه زمان می‌برد.

## 7. وضعیت Job

چرخه معمول:

```text
queued
  ↓
downloading
  ↓
compressing   (فقط compression jobs)
  ↓
uploading
  ↓
completed
```

در خطا:

```text
retry
  ↓
Pub/Sub redelivery
  ↓
downloading
```

بعد از `MAX_JOB_ATTEMPTS`:

```text
failed
```

Lease PostgreSQL از اجرای همزمان یک Job روی چند Worker جلوگیری می‌کند و heartbeat در Jobهای طولانی lease را تمدید می‌کند.

## 8. تست end-to-end

1. یک URL YouTube برای Bot بفرست.
2. کیفیت را انتخاب کن.
3. مقصد را انتخاب کن.
4. در Main لاگ Pub/Sub را ببین.
5. در Worker لاگ claim را ببین.
6. خروجی را در Telegram یا MediaFire بررسی کن.

## 9. نکته Telegram compression

ارسال یک Video به Bot به عنوان compression job ذخیره می‌شود:

```text
url = telegram:<file_id>
format = telegram_compress
destination = telegram
```

Worker فایل را دریافت می‌کند، با FFmpeg فشرده می‌کند و نتیجه را به همان chat برمی‌گرداند.

این نسخه به Local Telegram Bot API وابسته نیست.

## 10. امنیت

این موارد را commit نکن:

```text
terraform.tfvars
*.tfstate
session.json
youtube-cookies.txt
.env
```

Bot token، MediaFire session و YouTube cookies credential محسوب می‌شوند.

## 11. تغییر ظرفیت Worker

```bash
terraform apply -var='worker_min=1' -var='worker_max=20'
```

برای Workerهای قوی‌تر:

```hcl
machine_type_worker = "c3-standard-8"
```

چون هر Worker به صورت پیش‌فرض فقط یک Job همزمان اجرا می‌کند، افزایش تعداد VMها باعث افزایش parallelism می‌شود بدون اینکه یک VM را با چند دانلود همزمان overload کنیم.
