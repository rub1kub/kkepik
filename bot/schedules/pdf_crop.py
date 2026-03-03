# schedules/pdf_crop.py
"""
Кроп расписания группы из PDF — просто столбец группы как есть.
Используется при рассылке — каждый студент получает скриншот своих пар.
"""

import io
import logging
import pdfplumber
from collections import defaultdict
from .pdf_to_df import _find_group_positions

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

    try:
        pdf = pdfplumber.open(file_path)
    except Exception as e:
        logger.warning(f"pdf_crop: не удалось открыть {file_path}: {e}")
        return result

    with pdf:
        for page in pdf.pages:
            groups = _find_group_positions(page)
            if not groups:
                continue

            page_img = page.to_image(resolution=RENDER_DPI)
            pil_img = page_img.original
            scale = pil_img.width / page.width

            # Кластеризация групп в блоки по y (tolerance 3pt)
            blocks: dict[float, list] = defaultdict(list)
            sorted_groups = sorted(groups, key=lambda g: g["y"])
            cluster_y = None
            for g in sorted_groups:
                if cluster_y is None or abs(g["y"] - cluster_y) > 3:
                    cluster_y = g["y"]
                blocks[cluster_y].append(g)

            sorted_ys = sorted(blocks.keys())

            for bi, by in enumerate(sorted_ys):
                bg = sorted(blocks[by], key=lambda g: g["x"])

                y_top_pt = by - BLOCK_Y_PAD_TOP_PT
                if bi + 1 < len(sorted_ys):
                    y_bot_pt = sorted_ys[bi + 1] - BLOCK_Y_PAD_BOTTOM_PT
                else:
                    # Последний блок: ищем нижнюю границу контента в пределах блока
                    y_start = by + 3
                    block_x_left = bg[0]["x"] - GROUP_MARGIN_PT
                    block_x_right = bg[-1]["x"] + col_width + GROUP_MARGIN_PT
                    last_content_y = by
                    for c in page.chars:
                        if (c["top"] >= y_start and c["top"] <= page.height
                                and block_x_left <= c["x0"] <= block_x_right):
                            if c["top"] > last_content_y:
                                last_content_y = c["top"]
                    y_bot_pt = last_content_y + CONTENT_PAD_BOTTOM_PT

                y_top_px = max(0, int(y_top_pt * scale))
                y_bot_px = min(pil_img.height, int(y_bot_pt * scale))

                # Стандартная ширина столбца (из расстояния между группами)
                if len(bg) >= 2:
                    col_width = bg[1]["x"] - bg[0]["x"]
                else:
                    col_width = 130.0  # fallback

                for gi, g in enumerate(bg):
                    x_left_pt = g["x"] - GROUP_MARGIN_PT
                    if gi + 1 < len(bg):
                        x_right_pt = bg[gi + 1]["x"] - GROUP_MARGIN_PT
                    else:
                        # Последняя группа: ширина = стандартная ширина столбца
                        x_right_pt = min(g["x"] + col_width, page.width)

                    x_left_px = max(0, int(x_left_pt * scale))
                    x_right_px = min(pil_img.width, int(x_right_pt * scale))

                    cropped = pil_img.crop((x_left_px, y_top_px, x_right_px, y_bot_px))

                    buf = io.BytesIO()
                    cropped.save(buf, format="PNG", optimize=True)
                    result[g["name"].upper()] = buf.getvalue()

    return result
