import unittest

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from double_ctrl_translator.card import TranslationCard
from double_ctrl_translator.qt_ui import apply_style


class CardRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_style(cls.app)

    def test_markdown_renders_and_copy_uses_visible_text(self):
        card = TranslationCard(None, lambda: None)
        card.show(20, 20)
        try:
            card.update("**词条**：fiducial\n\n- fiducial point（参考点）")
            self.assertGreaterEqual(card.output.document().find("词条").charFormat().fontWeight(),
                                    QFont.Weight.Bold)
            self.assertNotIn("**", card.output.toPlainText())
            self.assertTrue(card.copy_button.isEnabled())
            card.copy()
            self.assertEqual(self.app.clipboard().text(), card.output.toPlainText())
        finally:
            card.close()


if __name__ == "__main__":
    unittest.main()
