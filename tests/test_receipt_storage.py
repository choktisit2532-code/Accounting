from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from personal_finance.config import Settings
from personal_finance.services import receipt_storage


def png_bytes(size=(2400, 1200)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color="white").save(output, format="PNG")
    return output.getvalue()


def supabase_settings():
    return SimpleNamespace(
        max_upload_bytes=5 * 1024 * 1024,
        max_receipt_pixels=40_000_000,
        max_receipt_dimension=1600,
        receipt_webp_quality=80,
        receipt_storage_backend="supabase",
        supabase_url="https://project.supabase.co",
        supabase_service_role_key="service-role-secret",
        supabase_storage_bucket="receipts",
    )


def test_supabase_receipt_upload_download_and_delete(monkeypatch):
    monkeypatch.setattr(receipt_storage, "settings", supabase_settings())
    calls = []

    def fake_post(url, *, content, headers, timeout):
        calls.append(("POST", url, headers, content))
        assert headers["Authorization"] == "Bearer service-role-secret"
        assert headers["Content-Type"] == "image/webp"
        with Image.open(BytesIO(content)) as image:
            assert image.format == "WEBP"
            assert max(image.size) == 1600
        return httpx.Response(200, request=httpx.Request("POST", url), json={"Key": "stored"})

    def fake_get(url, *, headers, timeout):
        calls.append(("GET", url, headers, None))
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            content=b"stored-webp",
            headers={"content-type": "image/webp"},
        )

    def fake_request(method, url, *, json, headers, timeout):
        calls.append((method, url, headers, json))
        return httpx.Response(200, request=httpx.Request(method, url), json=[])

    monkeypatch.setattr(receipt_storage.httpx, "post", fake_post)
    monkeypatch.setattr(receipt_storage.httpx, "get", fake_get)
    monkeypatch.setattr(receipt_storage.httpx, "request", fake_request)

    path = receipt_storage.save_receipt(png_bytes(), user_id=42)
    stored = receipt_storage.load_receipt(path)
    receipt_storage.delete_receipt(path)

    assert path.startswith("users/42/") and path.endswith(".webp")
    assert stored.data == b"stored-webp"
    assert stored.content_type == "image/webp"
    assert calls[0][1].startswith("https://project.supabase.co/storage/v1/object/receipts/users/42/")
    assert calls[1][1] == calls[0][1]
    assert calls[2][3] == {"prefixes": [path]}


def test_supabase_upload_permission_error_is_actionable(monkeypatch):
    monkeypatch.setattr(receipt_storage, "settings", supabase_settings())

    def fake_post(url, *, content, headers, timeout):
        return httpx.Response(
            403,
            request=httpx.Request("POST", url),
            json={"message": "new row violates row-level security policy"},
        )

    monkeypatch.setattr(receipt_storage.httpx, "post", fake_post)

    try:
        receipt_storage.save_receipt(png_bytes(), user_id=42)
        assert False, "expected ReceiptStorageError"
    except receipt_storage.ReceiptStorageError as exc:
        assert "SUPABASE_SERVICE_ROLE_KEY" in str(exc)


def test_new_supabase_secret_key_is_sent_with_required_storage_headers(monkeypatch):
    settings = supabase_settings()
    settings.supabase_service_role_key = "sb_secret_backend-key"
    monkeypatch.setattr(receipt_storage, "settings", settings)

    headers = receipt_storage._supabase_headers("image/webp")

    assert headers["apikey"] == "sb_secret_backend-key"
    assert headers["Authorization"] == "Bearer sb_secret_backend-key"


def test_supabase_upload_jws_error_is_actionable(monkeypatch):
    monkeypatch.setattr(receipt_storage, "settings", supabase_settings())

    def fake_post(url, *, content, headers, timeout):
        return httpx.Response(
            400,
            request=httpx.Request("POST", url),
            json={"statusCode": "403", "error": "Unauthorized", "message": "JWS Protected Header is invalid"},
        )

    monkeypatch.setattr(receipt_storage.httpx, "post", fake_post)

    try:
        receipt_storage.save_receipt(png_bytes(), user_id=42)
        assert False, "expected ReceiptStorageError"
    except receipt_storage.ReceiptStorageError as exc:
        assert "JWS Protected Header is invalid" in str(exc)
        assert "SUPABASE_SERVICE_ROLE_KEY" in str(exc)


def test_supabase_url_cannot_be_postgresql_connection_string():
    settings = Settings(
        app_env="production",
        secret_key="a-production-secret-that-is-long-enough",
        auto_create_tables=False,
        receipt_storage_backend="supabase",
        supabase_url="postgresql://postgres:password@db.example.com/postgres",
        supabase_service_role_key="service-role-secret",
        supabase_storage_bucket="receipts",
    )

    with pytest.raises(RuntimeError, match="HTTPS Project URL"):
        settings.validate()
