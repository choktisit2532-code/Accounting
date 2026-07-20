import io
import logging
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from personal_finance.config import settings


logger = logging.getLogger("personal_finance.receipts")
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


class ReceiptStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReceiptObject:
    data: bytes
    content_type: str


def compress_receipt(data: bytes) -> bytes:
    if len(data) > settings.max_upload_bytes:
        raise ValueError("ไฟล์ใบเสร็จมีขนาดใหญ่เกินกำหนด")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if (source.format or "").upper() not in ALLOWED_IMAGE_FORMATS:
                raise ValueError("รองรับเฉพาะไฟล์ JPEG, PNG และ WebP")
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.width * image.height > settings.max_receipt_pixels:
                raise ValueError("รูปภาพมีจำนวนพิกเซลสูงเกินกำหนด")
            image.thumbnail(
                (settings.max_receipt_dimension, settings.max_receipt_dimension),
                Image.Resampling.LANCZOS,
            )
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(
                output,
                format="WEBP",
                quality=settings.receipt_webp_quality,
                method=6,
                optimize=True,
            )
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("ไฟล์ใบเสร็จต้องเป็น JPEG, PNG หรือ WebP ที่ถูกต้อง") from exc


def _safe_object_path(path: str) -> str:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ReceiptStorageError("Invalid receipt object path")
    return candidate.as_posix()


def _supabase_headers(content_type: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_role_key
    # The Storage object endpoint requires both headers. Supabase's gateway
    # resolves new sb_secret_ keys as well as legacy service_role JWTs.
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _supabase_object_url(path: str) -> str:
    bucket = quote(settings.supabase_storage_bucket, safe="")
    object_path = quote(_safe_object_path(path), safe="/")
    return f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{object_path}"


def _save_local(path: str, data: bytes) -> None:
    root = settings.upload_dir.resolve()
    target = (root / _safe_object_path(path)).resolve()
    if not target.is_relative_to(root):
        raise ReceiptStorageError("Invalid local receipt path")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _upload_error_message(status_code: int, detail: str = "") -> str:
    if "JWS Protected Header is invalid" in detail or "JWS" in detail:
        return "Supabase Storage ปฏิเสธสิทธิ์ (JWS Protected Header is invalid) กรุณาตรวจสอบว่าตั้งค่า SUPABASE_SERVICE_ROLE_KEY ถูกต้องและไม่มีอักขระแปลกปลอม"
    if status_code in {401, 403}:
        return "Supabase Storage ปฏิเสธสิทธิ์ กรุณาตรวจ SUPABASE_SERVICE_ROLE_KEY"
    if status_code == 404:
        return f"ไม่พบ Supabase Storage bucket ชื่อ {settings.supabase_storage_bucket}"
    if status_code == 413:
        return "รูปมีขนาดใหญ่เกินข้อจำกัดของ Supabase Storage bucket"
    if status_code == 400:
        return "Supabase Storage ปฏิเสธไฟล์ กรุณาตรวจ MIME type และขนาดไฟล์ของ bucket"
    return f"ไม่สามารถจัดเก็บรูปใน Supabase Storage ได้ (HTTP {status_code})"


def save_receipt(data: bytes, user_id: int) -> str:
    compressed = compress_receipt(data)
    path = f"users/{user_id}/{uuid.uuid4().hex}.webp"
    if settings.receipt_storage_backend == "local":
        _save_local(path, compressed)
        return path
    try:
        response = httpx.post(
            _supabase_object_url(path),
            content=compressed,
            headers={**_supabase_headers("image/webp"), "x-upsert": "false", "Cache-Control": "3600"},
            timeout=20.0,
        )
        response.raise_for_status()
        return path
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Supabase receipt upload rejected path=%s status=%s response=%s",
            path,
            exc.response.status_code,
            exc.response.text[:1000],
        )
        status_code = exc.response.status_code
        detail = exc.response.text
        try:
            body = exc.response.json()
            if isinstance(body, dict):
                body_status = body.get("statusCode")
                body_error = str(body.get("error", ""))
                body_msg = str(body.get("message", ""))
                if body_status in {"403", 403, "401", 401} or "JWS" in body_msg or "Unauthorized" in body_error:
                    status_code = 403
                    if body_msg:
                        detail = body_msg
        except Exception:
            pass
        raise ReceiptStorageError(_upload_error_message(status_code, detail)) from exc
    except httpx.RequestError as exc:
        logger.exception("Supabase receipt upload connection failed path=%s", path)
        raise ReceiptStorageError("เชื่อมต่อ Supabase Storage ไม่สำเร็จ กรุณาตรวจ SUPABASE_URL") from exc


def load_receipt(path: str) -> ReceiptObject:
    safe_path = _safe_object_path(path)
    if settings.receipt_storage_backend == "local":
        root = settings.upload_dir.resolve()
        target = (root / safe_path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise FileNotFoundError(path)
        return ReceiptObject(target.read_bytes(), CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream"))
    try:
        response = httpx.get(
            _supabase_object_url(safe_path),
            headers=_supabase_headers(),
            timeout=20.0,
        )
        if response.status_code == 404:
            raise FileNotFoundError(path)
        response.raise_for_status()
        return ReceiptObject(response.content, response.headers.get("content-type", "image/webp"))
    except FileNotFoundError:
        raise
    except httpx.HTTPError as exc:
        logger.exception("Supabase receipt download failed path=%s", safe_path)
        raise ReceiptStorageError("ไม่สามารถเปิดรูปจาก Supabase Storage ได้") from exc


def delete_receipt(path: str | None) -> None:
    if not path:
        return
    safe_path = _safe_object_path(path)
    if settings.receipt_storage_backend == "local":
        root = settings.upload_dir.resolve()
        target = (root / safe_path).resolve()
        if target.is_relative_to(root):
            target.unlink(missing_ok=True)
        return
    try:
        bucket = quote(settings.supabase_storage_bucket, safe="")
        response = httpx.request(
            "DELETE",
            f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{bucket}",
            json={"prefixes": [safe_path]},
            headers=_supabase_headers("application/json"),
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Supabase receipt cleanup failed path=%s error=%s", safe_path, exc)
