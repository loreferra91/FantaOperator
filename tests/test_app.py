import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest


class AppNavigationTests(unittest.TestCase):
    def test_overview_shortcut_opens_lineup_without_widget_state_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"FANTAOPERATOR_DB": str(Path(directory) / "ui.db")}):
                st.cache_resource.clear()
                try:
                    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=10)
                    self.assertEqual(len(app.exception), 0)
                    next(button for button in app.button if "Ottimizza formazione" in button.label).click().run(timeout=10)
                    self.assertEqual(len(app.exception), 0)
                    self.assertEqual(app.session_state["page"], "▥  Formazione")
                    self.assertIn("Ottimizzatore formazione", [title.value for title in app.title])
                finally:
                    st.cache_resource.clear()


if __name__ == "__main__":
    unittest.main()
