from __future__ import annotations

import unittest

from app.routers.utils import (
    TELEGRAM_MESSAGE_MAX_CHARS,
    render_safe_parts,
    render_telegram_html,
)


class RenderSafePartsTests(unittest.TestCase):
    def test_special_chars_are_split_by_rendered_length(self) -> None:
        text = "<" * TELEGRAM_MESSAGE_MAX_CHARS
        parts = render_safe_parts(text)

        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= TELEGRAM_MESSAGE_MAX_CHARS for p in parts))
        self.assertEqual("".join(parts), render_telegram_html(text))

    def test_long_fenced_code_and_mixed_text(self) -> None:
        text = (
            "Начало\n"
            + "```python\n"
            + "print('<tag>')\n" * 2000
            + "```\n"
            + "Конец с **bold** и `inline`"
        )
        parts = render_safe_parts(text)

        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= TELEGRAM_MESSAGE_MAX_CHARS for p in parts))

    def test_heading_and_italic_are_rendered(self) -> None:
        text = "##1) Базовые команды\n*(в проде осторожно)*"
        rendered = render_telegram_html(text)

        self.assertIn("<b>1) Базовые команды</b>", rendered)
        self.assertIn("<i>(в проде осторожно)</i>", rendered)


if __name__ == "__main__":
    unittest.main()
