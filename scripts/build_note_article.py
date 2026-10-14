#!/usr/bin/env python3
from __future__ import annotations

import binascii
import json
import shutil
import struct
import zipfile
import zlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
NOTE = ROOT / "note"
LATEST = NOTE / "latest"
ARCHIVE = NOTE / "archive"
SITE_URL = "https://noranamani.github.io/noranamani-gadget-deals/"


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def yen(value) -> str:
    try:
        return f"¥{int(value):,}"
    except Exception:
        return "-"


def pct(value) -> str:
    if value is None:
        return ""
    return f"{float(value):.1f}%"


def product_index(products: dict) -> dict[str, dict]:
    out = {}
    for cat in products.get("categories", []):
        for item in cat.get("items", []):
            code = str(item.get("code") or "")
            if code and code not in out:
                out[code] = item
    return out


def history_text(item: dict, min_samples: int) -> str:
    samples = int(item.get("historySamples") or 0)
    baseline = item.get("baselinePrice")
    drop = item.get("dropPct")
    if samples < min_samples:
        return f"価格履歴: {samples}/{min_samples}回（判定準備中）"
    if baseline and drop is not None:
        if float(drop) >= 0:
            return f"価格履歴: 中央値 {yen(baseline)} / 現在 {yen(item.get('price'))} / {pct(drop)}安"
        return f"価格履歴: 中央値 {yen(baseline)} / 現在 {yen(item.get('price'))} / {pct(abs(float(drop)))}高"
    return f"価格履歴: {samples}回"


def validate_editorial(editorial: dict, known: dict) -> None:
    title = str(editorial.get("title") or "").strip()
    if not title:
        raise ValueError("note weekly title is empty")
    featured = editorial.get("featured") or []
    if not isinstance(featured, list) or len(featured) > 5:
        raise ValueError("note weekly featured must be a list of at most 5 items")
    for row in featured:
        code = str(row.get("code") or "")
        if code not in known:
            raise ValueError(f"unknown note featured code: {code}")
    hashtags = editorial.get("hashtags") or []
    if not isinstance(hashtags, list) or len(hashtags) > 10:
        raise ValueError("note hashtags must be a list of at most 10 tags")


FONT = {
    "A":["01110","10001","10001","11111","10001","10001","10001"],
    "D":["11110","10001","10001","10001","10001","10001","11110"],
    "E":["11111","10000","10000","11110","10000","10000","11111"],
    "G":["01111","10000","10000","10111","10001","10001","01110"],
    "L":["10000","10000","10000","10000","10000","10000","11111"],
    "S":["01111","10000","10000","01110","00001","00001","11110"],
    "T":["11111","00100","00100","00100","00100","00100","00100"],
    "W":["10001","10001","10001","10101","10101","10101","01010"],
    "K":["10001","10010","10100","11000","10100","10010","10001"],
    "Y":["10001","10001","01010","00100","00100","00100","00100"],
    "N":["10001","11001","11001","10101","10011","10011","10001"],
    "O":["01110","10001","10001","10001","10001","10001","01110"],
    "R":["11110","10001","10001","11110","10100","10010","10001"],
    "M":["10001","11011","10101","10101","10001","10001","10001"],
    "I":["11111","00100","00100","00100","00100","00100","11111"],
    "C":["01111","10000","10000","10000","10000","10000","01111"],
    "H":["10001","10001","10001","11111","10001","10001","10001"],
    "P":["11110","10001","10001","11110","10000","10000","10000"],
    "U":["10001","10001","10001","10001","10001","10001","01110"],
    "B":["11110","10001","10001","11110","10001","10001","11110"],
    "0":["01110","10001","10011","10101","11001","10001","01110"],
    "1":["00100","01100","00100","00100","00100","00100","01110"],
    "2":["01110","10001","00001","00010","00100","01000","11111"],
    "3":["11110","00001","00001","01110","00001","00001","11110"],
    "4":["00010","00110","01010","10010","11111","00010","00010"],
    "5":["11111","10000","10000","11110","00001","00001","11110"],
    "6":["01110","10000","10000","11110","10001","10001","01110"],
    "7":["11111","00001","00010","00100","01000","01000","01000"],
    "8":["01110","10001","10001","01110","10001","10001","01110"],
    "9":["01110","10001","10001","01111","00001","00001","01110"],
    "-":["00000","00000","00000","11111","00000","00000","00000"],
    "/":["00001","00010","00010","00100","01000","01000","10000"],
    " ":["00000"]*7,
}


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)


def draw_cover(path: Path, week_label: str) -> None:
    w, h = 1280, 670
    bg = (17, 24, 39)
    pixels = bytearray(bg * (w * h))

    def rect(x0, y0, x1, y1, color):
        x0 = max(0, x0); y0 = max(0, y0); x1 = min(w, x1); y1 = min(h, y1)
        r,g,b = color
        for y in range(y0, y1):
            start = (y*w + x0)*3
            for x in range(x0, x1):
                i = start + (x-x0)*3
                pixels[i:i+3] = bytes((r,g,b))

    def text(x, y, value, scale, color):
        cx = x
        for ch in value.upper():
            glyph = FONT.get(ch, FONT[" "])
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        rect(cx + gx*scale, y + gy*scale, cx + (gx+1)*scale, y + (gy+1)*scale, color)
            cx += 6*scale

    rect(55, 55, 1225, 615, (246, 248, 251))
    rect(85, 90, 1195, 180, (226, 238, 249))
    rect(85, 215, 615, 515, (255,255,255))
    rect(665, 215, 1195, 515, (255,255,255))
    for y in (270, 340, 410):
        rect(125, y, 560, y+42, (237,242,247))
        rect(705, y, 1140, y+42, (237,242,247))
        rect(145, y+14, 345, y+25, (18,100,163))
        rect(725, y+14, 925, y+25, (18,100,163))
    text(125, 112, "GADGET DEALS WEEKLY", 7, (15,52,90))
    text(145, 235, "PRICE WATCH", 5, (23,32,42))
    text(725, 235, "TOP PICKS", 5, (23,32,42))
    date_ascii = week_label.replace("〜", "-").replace("～", "-")
    text(125, 555, date_ascii[:23], 4, (230,236,244))

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        start = y*w*3
        raw.extend(pixels[start:start+w*3])
    data = zlib.compress(bytes(raw), 9)
    png = b"\x89PNG\r\n\x1a\n"
    png += png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += png_chunk(b"IDAT", data)
    png += png_chunk(b"IEND", b"")
    path.write_bytes(png)


def build_article(editorial: dict, products: dict) -> str:
    known = product_index(products)
    validate_editorial(editorial, known)
    min_samples = int(products.get("methodology", {}).get("dealMinSamples") or 5)

    lines = [f"# {editorial['title']}", "", str(editorial.get("intro") or "").strip(), ""]
    lines += [
        "## 今週のデータ",
        "",
        f"- 取得商品: {int(products.get('stats', {}).get('products', 0))}件",
        f"- 追跡商品: {int(products.get('stats', {}).get('tracked', 0))}件",
        f"- 特価候補: {int(products.get('stats', {}).get('deals', 0))}件",
        f"- 集計期間: {editorial.get('weekLabel', '')}",
        "",
    ]

    featured = editorial.get("featured") or []
    if featured:
        lines += ["## 今週の注目商品", ""]
        for idx, row in enumerate(featured, 1):
            item = known[str(row.get("code"))]
            lines += [
                f"### {idx}. {item.get('name', '')}",
                "",
                f"- カテゴリ: {item.get('category', '')}",
                f"- 現在価格: {yen(item.get('price'))}",
                f"- レビュー: ★{float(item.get('reviewAverage') or 0):.2f} / {int(item.get('reviewCount') or 0):,}件",
                f"- 比較スコア: {float(item.get('score') or 0):.1f}",
                f"- {history_text(item, min_samples)}",
                "",
                str(row.get("comment") or "").strip(),
                "",
                str(item.get("url") or ""),
                "",
            ]

    points = [str(x).strip() for x in editorial.get("points") or [] if str(x).strip()]
    if points:
        lines += ["## 今週のチェックポイント", ""]
        lines += [f"- {x}" for x in points]
        lines += [""]

    closing = str(editorial.get("closing") or "").strip()
    if closing:
        lines += ["## まとめ", "", closing, ""]

    lines += [
        "比較サイトはこちらです。",
        "",
        SITE_URL,
        "",
        "※この記事には楽天アフィリエイトのリンクが含まれます。価格・在庫・ポイント等は取得時点の情報です。購入前に販売ページをご確認ください。",
        "",
    ]

    hashtags = [str(x).strip().lstrip("#") for x in editorial.get("hashtags") or [] if str(x).strip()]
    if hashtags:
        lines.append(" ".join(f"#{tag}" for tag in hashtags))
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    products = load(DATA / "products.json", {})
    editorial = load(DATA / "note-weekly.json", {})
    if not editorial:
        print("note weekly editorial not found; skipping")
        return

    article = build_article(editorial, products)
    generated_at = str(editorial.get("generatedAt") or "")
    try:
        dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        day = dt.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    except ValueError:
        day = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

    archive_dir = ARCHIVE / day
    LATEST.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    latest_article = LATEST / "article.md"
    latest_cover = LATEST / "article-cover.png"
    latest_zip = LATEST / "note-post.zip"

    latest_article.write_text(article, encoding="utf-8")
    draw_cover(latest_cover, str(editorial.get("weekLabel") or day))

    shutil.copy2(latest_article, archive_dir / "article.md")
    shutil.copy2(latest_cover, archive_dir / "article-cover.png")

    with zipfile.ZipFile(latest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(latest_article, "article.md")
        zf.write(latest_cover, "article-cover.png")

    print(f"built note weekly bundle: {latest_zip}")


if __name__ == "__main__":
    main()
