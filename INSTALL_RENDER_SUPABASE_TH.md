# ติดตั้ง Smart Finance: GitHub + Render + Supabase

ระบบนี้ใช้เพียง 3 บริการ:

- **GitHub** เก็บโค้ด
- **Render** รันหน้าเว็บ, FastAPI และ LINE Webhook ใน Web Service เดียว
- **Supabase** เก็บ PostgreSQL และรูปใบเสร็จใน Private Storage

ไม่ต้องใช้ Netlify และไม่ต้องแยก Frontend ออกจาก Backend

## 1. เตรียม GitHub

นำไฟล์ทั้งหมดในโฟลเดอร์โครงการขึ้น GitHub โดยให้ `Dockerfile` และ
`render.yaml` อยู่ที่ root ของ repository ห้ามอัปโหลด `.env`, ฐานข้อมูล
`.db`, รูปใน `data/` หรือ `__pycache__` (ไฟล์ `.gitignore` กันไว้แล้ว)

## 2. เตรียม Supabase

### โครงการใหม่ที่ยังไม่มีข้อมูล

เปิด **Supabase Dashboard > SQL Editor > New query** แล้วคัดลอกไฟล์
`supabase_schema.sql` ไปวางและกด Run หนึ่งครั้ง ไฟล์นี้สร้างทั้งตาราง,
Alembic revision และ Private bucket ชื่อ `receipts`

> คำเตือน: ไฟล์นี้เป็น Fresh install และจะลบตาราง Smart Finance เดิม
> ถ้ามีข้อมูลอยู่แล้วต้องสำรองข้อมูลก่อน ห้ามรันซ้ำโดยไม่ตั้งใจ

### มีฐานข้อมูลเดิมและต้องการแก้เฉพาะ Storage

รันเฉพาะ `supabase_storage_setup.sql` ซึ่งไม่แตะรายการทางการเงิน

จากนั้นเตรียมค่า 3 ค่า:

1. `SUPABASE_URL` จาก **Project Settings > API > Project URL**  
   รูปแบบต้องเป็น `https://PROJECT.supabase.co`
2. `SUPABASE_SERVICE_ROLE_KEY` ใช้ **Secret key/service_role** ฝั่งเซิร์ฟเวอร์  
   ห้ามใช้ publishable/anon key และห้ามใส่ในหน้าเว็บหรือ GitHub
3. `PERSONAL_FINANCE_DATABASE_URL` จาก **Connect > Transaction pooler**  
   รูปแบบต้องเริ่มด้วย `postgresql://`

ห้ามสลับสอง URL นี้:

```text
SUPABASE_URL=https://PROJECT.supabase.co
PERSONAL_FINANCE_DATABASE_URL=postgresql://postgres.PROJECT:PASSWORD@HOST:6543/postgres
```

## 3. สร้าง Render Web Service

วิธีง่ายที่สุดคือเปิด **Render Dashboard > New > Blueprint** เลือก GitHub
repository นี้ แล้วให้ Render อ่าน `render.yaml`

กรอก Environment Variables ที่ Render ถาม:

| ตัวแปร | ค่าที่ต้องใส่ |
|---|---|
| `PERSONAL_FINANCE_DATABASE_URL` | Supabase Transaction pooler URL |
| `SUPABASE_URL` | Project URL ที่ขึ้นต้นด้วย `https://` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase secret/service-role key |
| `ALLOWED_ORIGINS` | URL Render จริง เช่น `https://ชื่อบริการ.onrender.com` |
| `LINE_CHANNEL_ACCESS_TOKEN` | Token จาก LINE Developers |
| `LINE_CHANNEL_SECRET` | Secret จาก LINE Developers |
| `GEMINI_API_KEY` | API key สำหรับอ่านข้อความ/รูป |

`SECRET_KEY` ให้ Blueprint สุ่มให้อัตโนมัติ ส่วนตัวแปรอื่นมีค่าเหมาะสมอยู่ใน
`render.yaml` แล้ว เมื่อ Deploy สำเร็จ Render จะรัน Alembic ก่อนเปิด FastAPI
และ Uvicorn จะ bind พอร์ตจาก `$PORT` อัตโนมัติ

ถ้า URL ของ Render เพิ่งถูกสร้างหลัง Blueprint ให้กลับไปแก้
`ALLOWED_ORIGINS` เป็น URL นั้นแล้วกด **Manual Deploy > Deploy latest commit**

## 4. ตั้ง LINE Webhook

ใน LINE Developers ตั้ง Webhook URL เป็น:

```text
https://ชื่อบริการ.onrender.com/api/line/webhook
```

กด Verify และเปิด **Use webhook** จากนั้นเข้าหน้าเว็บ ขอรหัสผูกบัญชี แล้วส่ง
`ผูกบัญชี PF-XXXXXXXX` ให้บอต

คำสั่งสรุปที่รองรับ:

```text
ขอสรุปเดือนนี้
ขอสรุปเดือนกรกฎาคม 2569
สรุปเดือน 7/2569
ขอสรุปปีนี้
ขอสรุปปี 2569
```

คำสั่งสรุปอ่านยอดจริงจาก PostgreSQL โดยตรง ไม่เรียก Gemini และไม่สร้าง
รายการรอยืนยัน

## 5. ตรวจหลังติดตั้ง

เปิด `https://ชื่อบริการ.onrender.com/health` ต้องได้ `status: ok` และ build
`20260720.3` จากนั้นทดสอบสมัครสมาชิก, เพิ่มบัญชี, ผูก LINE, ส่งรายการ และ
ขอสรุปผ่าน LINE

ถ้า `/health` ไม่ผ่าน ให้ตรวจตามลำดับ:

1. `PERSONAL_FINANCE_DATABASE_URL` ต้องเป็น `postgresql://...`
2. `SUPABASE_URL` ต้องเป็น `https://...supabase.co`
3. `SUPABASE_SERVICE_ROLE_KEY` ต้องเป็น Secret/service-role key
4. ตาราง `alembic_version` ต้องมีค่า `20260716_0002`
5. Storage ต้องมี Private bucket ชื่อ `receipts`
