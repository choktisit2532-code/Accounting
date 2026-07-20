-- Run this file only when the database tables already exist and you only need
-- to create/repair Supabase Storage. For a fresh install use supabase_schema.sql.
-- The bucket is private. FastAPI validates and compresses images before upload.

INSERT INTO storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
VALUES (
    'receipts',
    'receipts',
    false,
    NULL,
    NULL
)
ON CONFLICT (id) DO UPDATE
SET
    public = false,
    file_size_limit = NULL,
    allowed_mime_types = NULL;
