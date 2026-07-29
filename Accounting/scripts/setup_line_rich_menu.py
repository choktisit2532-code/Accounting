"""Create and activate the Smart Finance LINE rich menu."""
import io
import json
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

# The Docker image installs this repository as /app/personal_finance, while
# GitHub Actions checks it out as the repository root.  Add the repository root
# for direct execution (`python scripts/setup_line_rich_menu.py`) and retain the
# package import for Docker/Render.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

try:
    from personal_finance.config import BASE_DIR, settings
except ModuleNotFoundError as exc:
    if exc.name != "personal_finance":
        raise
    from config import BASE_DIR, settings


SIZE = (2500, 1686)
LABELS = (
    ("เงินอยู่ที่ไหน", "message", "เงินอยู่ที่ไหน"),
    ("สรุปรายรับ", "message", "สรุปรายรับเดือนนี้"),
    ("สรุปรายจ่าย", "message", "สรุปรายจ่ายเดือนนี้"),
    ("สรุปกำไร", "message", "สรุปกำไรเดือนนี้"),
    ("ถ่ายรูป", "message", "วิธีส่งรูปใบเสร็จ"),
    ("บันทึกรายการ", "uri", "/dashboard?action=transaction"),
    ("รถและค่าใช้จ่าย", "uri", "/fleet"),
    ("ตั้งค่า", "uri", "/dashboard?action=settings"),
)


def _font(size: int, bold: bool = False):
    name = "IBMPlexSansThaiLooped-Bold.ttf" if bold else "IBMPlexSansThaiLooped-Regular.ttf"
    return ImageFont.truetype(str(BASE_DIR / "assets" / "fonts" / name), size)


def build_image() -> bytes:
    image = Image.new("RGB", SIZE, "#F4F7FB")
    draw = ImageDraw.Draw(image)
    colors = ("#3157F6", "#13A89E", "#FF7A59", "#7B61FF",
              "#E94C67", "#00A6A6", "#17356E", "#596780")
    # Keep icon text inside the bundled Thai font's supported glyph set.
    icons = ("฿", "+", "−", "=", "รูป", "+", "รถ", "...")
    cell_w, cell_h = SIZE[0] // 4, SIZE[1] // 2
    for index, ((label, _, _), color) in enumerate(zip(LABELS, colors)):
        col, row = index % 4, index // 4
        box = (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
        draw.rectangle(box, fill=color)
        cx = (box[0] + box[2]) // 2
        icon_font = _font(104, True)
        ib = draw.textbbox((0, 0), icons[index], font=icon_font)
        draw.text((cx - (ib[2] - ib[0]) / 2, box[1] + 215), icons[index], font=icon_font, fill="white")
        label_font = _font(48, True)
        lb = draw.textbbox((0, 0), label, font=label_font)
        draw.text((cx - (lb[2] - lb[0]) / 2, box[3] - 190), label, font=label_font, fill="white")
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def rich_menu_payload() -> dict:
    cell_w, cell_h = SIZE[0] // 4, SIZE[1] // 2
    areas = []
    for index, (label, kind, value) in enumerate(LABELS):
        action = {"type": kind, "label": label}
        action["text" if kind == "message" else "uri"] = (
            value if kind == "message" else f"{settings.public_base_url}{value}"
        )
        areas.append({
            "bounds": {"x": (index % 4) * cell_w, "y": (index // 4) * cell_h,
                       "width": cell_w, "height": cell_h},
            "action": action,
        })
    return {"size": {"width": SIZE[0], "height": SIZE[1]}, "selected": True,
            "name": "Accounting + Fleet Main", "chatBarText": "เมนูบัญชี", "areas": areas}


def main() -> None:
    if not settings.line_channel_access_token:
        raise SystemExit("LINE_CHANNEL_ACCESS_TOKEN is required")
    headers = {"Authorization": f"Bearer {settings.line_channel_access_token}"}
    image_bytes = build_image()
    with httpx.Client(timeout=30.0) as client:
        created = client.post(
            "https://api.line.me/v2/bot/richmenu",
            headers={**headers, "Content-Type": "application/json"},
            content=json.dumps(rich_menu_payload(), ensure_ascii=False).encode(),
        )
        created.raise_for_status()
        rich_menu_id = created.json()["richMenuId"]
        client.post(
            f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
            headers={**headers, "Content-Type": "image/png"}, content=image_bytes,
        ).raise_for_status()
        client.post(
            f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}", headers=headers,
        ).raise_for_status()
    Path(BASE_DIR / "static" / "line-rich-menu.png").write_bytes(image_bytes)
    print(f"Rich menu activated: {rich_menu_id}")


if __name__ == "__main__":
    main()
