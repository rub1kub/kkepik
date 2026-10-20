# schedules/pdf_crop.py
"""
Кроп расписания группы из PDF — просто столбец группы как есть.
Используется при рассылке — каждый студент получает скриншот своих пар.
"""

import io
import logging
import pdfplumber
from collections import defaultdict
from PIL import Image
from .pdf_to_df import _iter_pdf_block_segments

logger = logging.getLogger(__name__)

RENDER_DPI = 200
GROUP_MARGIN_PT = 5.0
BLOCK_Y_PAD_TOP_PT = 3.0
BLOCK_Y_PAD_BOTTOM_PT = 3.0
CONTENT_PAD_BOTTOM_PT = 15.0  # отступ ниже последнего контента
CONTENT_PAD_RIGHT_PT = 5.0    # отступ правее последнего контента


def crop_group_screenshots(file_path: str) -> dict[str, bytes]:
    """
    Рендерит PDF и кропает столбец каждой группы (от имени до конца пар).

    Возвращает {GROUP_NAME_UPPER: PNG bytes}.
    Каждая страница рендерится ровно один раз.
    """
    result: dict[str, bytes] = {}
    fragments: dict[str, list[bytes]] = defaultdict(list)

    try:
        pdf = pdfplumber.open(file_path)
    except Exception as e:
        logger.warning(f"pdf_crop: не удалось открыть {file_path}: {e}")
        return result

    with pdf:
        rendered_page = None
        pil_img = None
        try:
            for segment in _iter_pdf_block_segments(pdf):
                page = segment.page
                if rendered_page is not page:
                    if pil_img is not None:
                        pil_img.close()
                    pil_img = page.to_image(resolution=RENDER_DPI).original
                    rendered_page = page
                scale = pil_img.width / page.width
                bg = segment.groups
                col_width = bg[1]["x"] - bg[0]["x"] if len(bg) >= 2 else 130.0
                header_y = bg[0]["y"]
                y_top_pt = 0 if segment.continuation else header_y - BLOCK_Y_PAD_TOP_PT
                y_bot_pt = segment.y_end
                if segment.y_end >= page.height - 5:
                    block_x_left = bg[0]["x"] - GROUP_MARGIN_PT
                    block_x_right = bg[-1]["x"] + col_width + GROUP_MARGIN_PT
                    last_content_y = segment.y_start if segment.continuation else header_y
                    for char in page.chars:
                        if (segment.y_start <= char["top"] <= page.height
                                and block_x_left <= char["x0"] <= block_x_right):
                            last_content_y = max(last_content_y, char["top"])
                    y_bot_pt = last_content_y + CONTENT_PAD_BOTTOM_PT
                y_top_px = max(0, int(y_top_pt * scale))
                y_bot_px = min(pil_img.height, int(y_bot_pt * scale))
                if y_bot_px <= y_top_px:
                    continue
                for index, group in enumerate(bg):
                    name = group["name"].upper()
                    if not segment.continuation:
                        # An explicit later block retains the legacy final-block
                        # behavior; only genuine page continuations are joined.
                        fragments[name] = []
                    x_left_pt = group["x"] - GROUP_MARGIN_PT
                    x_right_pt = (bg[index + 1]["x"] - GROUP_MARGIN_PT
                                  if index + 1 < len(bg)
                                  else min(group["x"] + col_width, page.width))
                    cropped = pil_img.crop((max(0, int(x_left_pt * scale)), y_top_px,
                                            min(pil_img.width, int(x_right_pt * scale)), y_bot_px))
                    try:
                        buf = io.BytesIO()
                        cropped.save(buf, format="PNG", optimize=True)
                        fragments[name].append(buf.getvalue())
                    finally:
                        cropped.close()
        finally:
            if pil_img is not None:
                pil_img.close()

    for name, parts in fragments.items():
        if len(parts) == 1:
            result[name] = parts[0]
            continue
        images = []
        joined = None
        try:
            images = [Image.open(io.BytesIO(part)) for part in parts]
            joined = Image.new("RGB", (max(image.width for image in images),
                                       sum(image.height for image in images)), "white")
            top = 0
            for image in images:
                joined.paste(image, (0, top))
                top += image.height
            buf = io.BytesIO()
            joined.save(buf, format="PNG", optimize=True)
            result[name] = buf.getvalue()
        finally:
            for image in images:
                image.close()
            if joined is not None:
                joined.close()

    return result
