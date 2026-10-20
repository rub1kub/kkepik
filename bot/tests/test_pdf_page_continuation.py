import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from schedules.group_schedule import get_schedule_for_group
from schedules.knowledge_store import extract_lessons
from schedules.pdf_crop import crop_group_screenshots
from schedules.pdf_to_df import _iter_pdf_block_segments, pdf_to_dataframe


GROUP_A = "116-КД9-3ССА"
GROUP_B = "117-КД9-3ИНС"


def char(text, x, top):
    return {"text": text, "x0": x, "x1": x + max(4, len(text) * 4),
            "top": top, "fontname": "fixture"}


def lesson(number, top, *, shift=0, subject=None):
    return [char(str(number), 40 + shift, top),
            char(subject or f"Предмет {number}", 100 + shift, top),
            char("58", 180 + shift, top),
            char("Искандарян К.С.", 100 + shift, top + 10)]


class FakePage:
    def __init__(self, number, headers=(), chars=(), height=120):
        self.page_number = number
        self.width = 300
        self.height = height
        self.headers = list(headers)
        self.chars = list(chars) + [char(name, x, top) for name, x, top in headers]
        self.render_count = 0

    def extract_words(self, **kwargs):
        return [{"text": name, "x0": x, "top": top} for name, x, top in self.headers]

    def to_image(self, **kwargs):
        self.render_count += 1
        return SimpleNamespace(original=Image.new("RGB", (self.width, self.height), "white"))


class FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def parse_pages(pages):
    with patch("schedules.pdf_to_df.pdfplumber.open", return_value=FakePdf(pages)):
        return pdf_to_dataframe("fixture.pdf")


class PdfPageContinuationTest(unittest.TestCase):
    def test_tail_before_new_headers_is_appended_without_repeating_header(self):
        pages = [FakePage(1, [(GROUP_A, 100, 90)], lesson(1, 100)),
                 FakePage(2, [(GROUP_B, 100, 50)], lesson(2, 0) + lesson(1, 70))]
        df = parse_pages(pages)
        self.assertEqual(sum(value == GROUP_A for value in df[2]), 1)
        lines_a = "\n".join(get_schedule_for_group(df, GROUP_A))
        self.assertIn("1 пара – Предмет 1", lines_a)
        self.assertIn("2 пара – Предмет 2", lines_a)
        self.assertNotIn("2 пара", "\n".join(get_schedule_for_group(df, GROUP_B)))
        _, lessons = extract_lessons(df, "2026-10-13")
        self.assertEqual([row["pair_number"] for row in lessons if row["group"] == GROUP_A], [1, 2])

    def test_heading_only_at_bottom_keeps_next_pages_rows(self):
        df = parse_pages([FakePage(1, [(GROUP_A, 100, 105)]),
                          FakePage(2, [(GROUP_B, 100, 50)], lesson(1, 0) + lesson(1, 70))])
        self.assertIn("1 пара – Предмет 1", "\n".join(get_schedule_for_group(df, GROUP_A)))
        self.assertEqual(sum(value == GROUP_A for value in df[2]), 1)

    def test_multiple_headerless_pages_continue_the_same_block(self):
        df = parse_pages([FakePage(1, [(GROUP_A, 100, 90)], lesson(1, 100)),
                          FakePage(2, chars=lesson(2, 0) + lesson(3, 100)),
                          FakePage(3, chars=lesson(4, 0))])
        lines = "\n".join(get_schedule_for_group(df, GROUP_A))
        for number in range(1, 5):
            self.assertIn(f"{number} пара – Предмет {number}", lines)
        self.assertEqual(sum(value == GROUP_A for value in df[2]), 1)

    def test_horizontal_shift_follows_the_pair_column_and_new_headers(self):
        df = parse_pages([FakePage(1, [(GROUP_A, 100, 90)], lesson(1, 100)),
                          FakePage(2, [(GROUP_B, 110, 50)],
                                   lesson(2, 0, shift=10) + lesson(1, 70, shift=10))])
        self.assertIn("2 пара – Предмет 2", "\n".join(get_schedule_for_group(df, GROUP_A)))

    def test_ordinary_pages_remain_separate(self):
        df = parse_pages([FakePage(1, [(GROUP_A, 100, 20)], lesson(1, 40), height=200),
                          FakePage(2, [(GROUP_B, 100, 20)], lesson(1, 40), height=200)])
        self.assertEqual(get_schedule_for_group(df, GROUP_A), get_schedule_for_group(df, GROUP_B))
        self.assertEqual(len(list(_iter_pdf_block_segments(FakePdf([
            FakePage(1, [(GROUP_A, 100, 20)], lesson(1, 40), height=200),
            FakePage(2, [(GROUP_B, 100, 20)], lesson(1, 40), height=200),
        ])))), 2)

    def test_nonsequential_or_misaligned_tail_fails_closed(self):
        for pages in (
            [FakePage(1, [(GROUP_A, 100, 90)], lesson(1, 100)),
             FakePage(2, [(GROUP_B, 100, 50)], lesson(1, 0))],
            [FakePage(1, [(GROUP_A, 100, 90)], lesson(1, 100)),
             FakePage(2, [(GROUP_B, 130, 50)], lesson(2, 0))],
        ):
            with self.subTest(pages=pages), self.assertRaisesRegex(ValueError, "неоднозначное продолжение"):
                parse_pages(pages)

    def test_unknown_group_guard_also_applies_to_continuation_pages(self):
        with self.assertRaisesRegex(ValueError, "нераспознанные заголовки"):
            parse_pages([FakePage(1, [(GROUP_A, 100, 90)], lesson(1, 100)),
                         FakePage(2, [("103-ОЧНО-3ИНС", 100, 50)], lesson(2, 0))])

    def test_crop_joins_header_and_tail_and_renders_each_page_once(self):
        pages = [FakePage(1, [(GROUP_A, 100, 90)], lesson(1, 100)),
                 FakePage(2, [(GROUP_B, 100, 50)], lesson(2, 0) + lesson(1, 70))]
        with patch("schedules.pdf_crop.pdfplumber.open", return_value=FakePdf(pages)):
            crops = crop_group_screenshots("fixture.pdf")
        with Image.open(io.BytesIO(crops[GROUP_A])) as image:
            self.assertEqual(image.height, (120 - 87) + 47)
        self.assertEqual([page.render_count for page in pages], [1, 1])

    def test_crop_preserves_heading_only_segment(self):
        pages = [FakePage(1, [(GROUP_A, 100, 105)]),
                 FakePage(2, chars=lesson(1, 0))]
        with patch("schedules.pdf_crop.pdfplumber.open", return_value=FakePdf(pages)):
            crops = crop_group_screenshots("fixture.pdf")
        with Image.open(io.BytesIO(crops[GROUP_A])) as image:
            self.assertGreater(image.height, 25)


class ActualOctober13PdfRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parents[1] / "data" / "Расписание на 13.10.2026_ГРУППЫ.pdf"
        if not path.exists():
            raise unittest.SkipTest("Production regression PDF is not available")
        cls.df = pdf_to_dataframe(str(path))
        cls.groups, cls.lessons = extract_lessons(cls.df, "2026-10-13")

    def test_116_has_all_four_pairs_and_correct_rooms(self):
        rows = [row for row in self.lessons if row["group"] == GROUP_A]
        self.assertEqual([row["pair_number"] for row in rows], [1, 2, 3, 4])
        self.assertEqual([row["audiences"] for row in rows], [["58"], ["59"], ["58"], ["58"]])
        self.assertEqual([row["teacher"] for row in rows],
                         ["Искандарян К.С.", "Гринь И.В.", "Искандарян К.С.", "Искандарян К.С."])

    def test_other_groups_in_first_page_break_are_complete(self):
        expected = {"112-Д9-3РЭС": [4, 5, 6], "113-КД9-3КСК": [1, 2, 3],
                    "114-КД9-3КСК": [1, 1, 2, 3]}
        for group, pairs in expected.items():
            with self.subTest(group=group):
                self.assertEqual([row["pair_number"] for row in self.lessons if row["group"] == group], pairs)

    def test_header_only_second_page_break_restores_four_groups(self):
        expected = {"146-Д9-1РУПО": [2, 3], "147-Д9-1БТС": [1, 2, 3],
                    "148-Д9-1ИНБ": [1, 2, 3], "149-Д9-1РЭС": [2, 3, 4]}
        for group, pairs in expected.items():
            with self.subTest(group=group):
                self.assertIn(group, self.groups)
                self.assertEqual([row["pair_number"] for row in self.lessons if row["group"] == group], pairs)


if __name__ == "__main__":
    unittest.main()
