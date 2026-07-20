# Smart Finance 2.0

ระบบบัญชีส่วนบุคคลแบบ FastAPI + Vanilla JavaScript พร้อม LINE Bot และ Gemini โดยธุรกรรมที่ AI วิเคราะห์ต้องผ่านการยืนยันก่อนบันทึกจริง

## ฟังก์ชันหลัก

- สมัครและเข้าสู่ระบบด้วย Session Cookie แบบ HttpOnly
- บัญชีเงินสด ธนาคาร บัตรเครดิต การลงทุน และบัญชีอื่น
- รายรับ รายจ่าย โอนเงิน แก้ไข และลบโดยคืน/คำนวณยอดอัตโนมัติ
- ปรับยอดบัญชีแบบมี Adjustment Transaction ตรวจสอบย้อนหลังได้
- ใบเสร็จส่วนตัว ย่อและแปลงเป็น WebP แล้วเก็บใน Supabase Storage แบบ Private
- หมวดหมู่ งบประมาณรายเดือน และเป้าหมายออม
- Dashboard รายเดือน พร้อมสลับกราฟกระแสเงินเป็นรายวัน 1–31 หรือรายเดือน ม.ค.–ธ.ค. อัตราออม และสัดส่วนรายจ่าย
- หน้า Dashboard แบบอ่านง่าย: เลือกเดือนก่อน–ถัดไป และสลับแท็บรายจ่าย/รายรับ/โอนเงิน โดยยอดรวม กราฟ และรายการใช้ข้อมูลชุดเดียวกัน
- ประวัติพร้อมค้นหา กรอง และส่งออกรายงาน PDF พร้อมยอดสรุป
- LINE Pairing พร้อมยกเลิกการเชื่อมต่อ
- ขอรายงานยอดจริงผ่าน LINE ได้ทั้งรายเดือนและรายปี โดยไม่ผ่าน Gemini
- ตรวจ LINE Signature และบันทึก Event Idempotency ในฐานข้อมูล
- Gemini วิเคราะห์ข้อความ/รูป แล้วให้ผู้ใช้ยืนยัน แก้ยอด หรือยกเลิก

## โครงสร้าง

    personal_finance/
    ├── main.py
    ├── config.py
    ├── db.py
    ├── models.py
    ├── schemas.py
    ├── security.py
    ├── routers/
    ├── services/
    ├── static/
    ├── migrations/
    ├── tests/
    ├── Dockerfile
    ├── render.yaml
    └── requirements.txt

## เริ่มใช้งานในเครื่อง

ต้องใช้ Python 3.12

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements-dev.txt
    cp .env.example .env

ตั้งค่า Secret อย่างน้อย 32 ตัวอักษร จากนั้นรันจากโฟลเดอร์ที่เป็น parent ของ personal_finance:

    uvicorn personal_finance.main:app --reload

เปิด http://localhost:8000

โหมด Development สามารถใช้ AUTO_CREATE_TABLES=true ได้ แต่ Production ต้องใช้ Alembic

## Database Migration

สร้างฐานข้อมูลใหม่หรืออัปเกรด schema:

    alembic -c personal_finance/alembic.ini upgrade head

ตรวจสอบ revision:

    alembic -c personal_finance/alembic.ini current

ก่อนย้ายฐานข้อมูลเดิม ให้สำรองข้อมูลก่อนเสมอ ฐานข้อมูลที่สร้างจากรุ่นต้นแบบซึ่งไม่มี Alembic ควรทดสอบการย้ายในสำเนาฐานข้อมูลก่อน Production

## Environment Variables

| ตัวแปร | Production | รายละเอียด |
|---|---:|---|
| APP_ENV | จำเป็น | ตั้งเป็น production |
| PERSONAL_FINANCE_DATABASE_URL หรือ DATABASE_URL | จำเป็น | PostgreSQL หรือ SQLite |
| SECRET_KEY | จำเป็น | อย่างน้อย 32 ตัวอักษร |
| AUTO_CREATE_TABLES | จำเป็น | Production ต้องเป็น false |
| ALLOWED_ORIGINS | จำเป็น | Domain หน้าเว็บ คั่นหลายค่าด้วย comma |
| MAX_UPLOAD_BYTES | ไม่บังคับ | ค่าเริ่มต้น 5 MB |
| MAX_RECEIPT_DIMENSION | ไม่บังคับ | ด้านยาวสูงสุดหลังย่อ ค่าเริ่มต้น 1,600 px |
| RECEIPT_WEBP_QUALITY | ไม่บังคับ | คุณภาพ WebP ค่าเริ่มต้น 80 |
| RECEIPT_STORAGE_BACKEND | จำเป็น | Production ใช้ supabase; local ใช้พัฒนา/ทดสอบ |
| SUPABASE_URL | เมื่อใช้ Storage | Project URL เช่น https://PROJECT.supabase.co |
| SUPABASE_SERVICE_ROLE_KEY | เมื่อใช้ Storage | เก็บเฉพาะฝั่ง Render ห้ามส่งไปหน้าเว็บ |
| SUPABASE_STORAGE_BUCKET | เมื่อใช้ Storage | ค่าเริ่มต้น receipts |
| LINE_CHANNEL_ACCESS_TOKEN | เมื่อใช้ LINE | Access token จาก LINE Developers |
| LINE_CHANNEL_SECRET | เมื่อใช้ LINE | ใช้ตรวจ webhook signature |
| GEMINI_API_KEY | เมื่อใช้ AI | API key สำหรับ Gemini |
| GEMINI_MODEL | เมื่อใช้ AI | ชื่อโมเดลที่บัญชีเข้าถึงได้ |

ห้าม commit .env, API key หรือฐานข้อมูลลง Git

## ตั้งค่า Supabase Storage

1. เปิด Supabase SQL Editor และรัน `supabase_storage_setup.sql` หนึ่งครั้ง
2. ตั้ง Environment Variables บน Render: `RECEIPT_STORAGE_BACKEND=supabase`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` และ `SUPABASE_STORAGE_BUCKET=receipts`
3. Deploy ใหม่ ระบบจะย่อรูปด้านยาวไม่เกิน 1,600 px ลบ EXIF และแปลงเป็น WebP ก่อนอัปโหลด
4. Bucket ต้องเป็น Private และห้ามนำ Service Role Key ไปใส่ใน JavaScript หรือ commit ลง Git

ฐานข้อมูลเก็บเฉพาะ object path รูปจะเปิดผ่าน API ที่ตรวจเจ้าของธุรกรรมแล้วเท่านั้น เมื่อลบธุรกรรม ระบบจะลบ object ตามไปด้วย

## ตั้งค่า LINE

1. สร้าง Messaging API Channel
2. ตั้ง Webhook URL เป็น https://YOUR-DOMAIN/api/line/webhook
3. เปิด Use webhook
4. ตั้ง LINE_CHANNEL_ACCESS_TOKEN และ LINE_CHANNEL_SECRET
5. ตั้ง Gemini key และตรวจชื่อโมเดลที่บัญชีใช้งานได้
6. เข้าหน้า Dashboard ขอรหัสจับคู่
7. ส่งคำว่า ผูกบัญชี PF-XXXXXXXX ให้ Bot

เมื่อส่งข้อความหรือรูป:

1. ระบบส่งข้อมูลให้ Gemini วิเคราะห์
2. สร้าง Pending Transaction อายุ 15 นาที
3. LINE แสดงปุ่มยืนยัน แก้ยอดเงิน และยกเลิก
4. ยอดบัญชีเปลี่ยนเฉพาะเมื่อกดยืนยัน

ข้อความและรูปการเงินจะถูกส่งไปยัง Gemini จึงควรแจ้งนโยบายความเป็นส่วนตัวและขอความยินยอมผู้ใช้ก่อนเปิดใช้ในองค์กร

คำสั่งรายงาน เช่น `ขอสรุปเดือนนี้`, `สรุปเดือน 7/2569`, `ขอสรุปปีนี้`
และ `ขอสรุปปี 2569` จะอ่านยอดจาก PostgreSQL โดยตรง ไม่ส่งให้ Gemini
และไม่สร้างรายการรอยืนยัน

## Deploy บน Render

render.yaml เตรียม Docker Runtime, Health check, Alembic migration, Supabase Storage และรายการ Environment variables

คู่มือติดตั้งทีละขั้นสำหรับโครงสร้าง GitHub + Render + Supabase อยู่ที่
[`INSTALL_RENDER_SUPABASE_TH.md`](INSTALL_RENDER_SUPABASE_TH.md) โดยไม่ใช้ Netlify

ตั้ง PERSONAL_FINANCE_DATABASE_URL เป็น Supabase/PostgreSQL connection string และ ALLOWED_ORIGINS เป็น URL จริงของบริการ

หากแยก Frontend คนละ Domain ต้องเพิ่ม Origin นั้นอย่างชัดเจน ห้ามใช้ wildcard ร่วมกับ Cookie Authentication

## การทดสอบ

    pytest -q
    ruff check .
    node --check static/app.js

ชุดทดสอบครอบคลุม Authentication, Ledger, เพิ่ม/แก้ไข/ลบธุรกรรม, Reconciliation, Supabase Storage, สิทธิ์ใบเสร็จ, LINE Signature, งบประมาณ และกราฟรายวัน

## หลักความถูกต้องของยอดเงิน

- ห้ามแก้ accounts.balance โดยตรงจากหน้าเว็บ
- ยอดตั้งต้นสร้าง Transaction ประเภท System
- การปรับยอดสร้าง Reconciliation Transaction
- การแก้ Transaction ย้อนผลรายการเดิมก่อนใช้ค่าใหม่
- การลบ Transaction คืนผลกระทบต่อบัญชี
- บัญชีที่มีประวัติจะไม่ถูกลบ
- ใช้ Decimal และ Numeric(14,2)
- PostgreSQL ใช้ row locking ตอนเปลี่ยนยอด

## ความปลอดภัย

- Password ใช้ bcrypt cost 12
- JWT อยู่ใน HttpOnly/SameSite Cookie
- Production บังคับ Secret ขั้นต่ำ
- Login, Register, Pairing และ Webhook มี rate-limit
- LINE ตรวจ HMAC-SHA256 Signature
- ใบเสร็จไม่เปิดผ่าน Static directory
- ตรวจไฟล์ด้วย Pillow รองรับเฉพาะ JPEG, PNG, WebP
- Dynamic UI escape ข้อมูลก่อนสร้าง HTML
- จำกัด CORS และ Origin
- Security headers และ CSP
- API ไม่ส่งรายละเอียด Exception ภายในให้ผู้ใช้

Rate limiter ในแอปเป็น safety net ระดับ process หากขยายหลาย instance ควรเพิ่ม Redis หรือ rate limiting ที่ edge/WAF

## Demo Data

ไม่มีรหัสทดสอบตายตัว หากต้องการข้อมูลตัวอย่าง:

    SEED_DEMO_DATA=true DEMO_PASSWORD=your-password python -m personal_finance.seed_data

ห้ามเปิด Demo Seed ใน Production
