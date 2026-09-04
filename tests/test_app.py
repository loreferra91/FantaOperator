import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest
from fantaoperator.database import Database


class AppNavigationTests(unittest.TestCase):
    def test_selected_day_drives_sidebar_and_roster_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "day.db"
            db = Database(path)
            db.import_records(1, 2, [{"player": "Test candidato", "role": "POR", "vote": 6}],
                              source_name="Fantacalcio.it", source_url="", payload_hash="test", default_status="PROVVISORIO")
            with patch.dict("os.environ", {"FANTAOPERATOR_DB": str(path)}):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
                next(s for s in app.selectbox if s.label == "Giornata").set_value(2).run()
                self.assertTrue(any("Stato: PROVVISORIO" in m.value for m in app.sidebar.markdown))
                app.sidebar.radio[0].set_value("♙  Rosa").run()
                self.assertEqual(len(app.exception), 0)
                candidates = next(s for s in app.selectbox if s.label == "Giocatore disponibile")
                self.assertEqual(candidates.options, ["Test candidato"])

    def test_every_page_handles_empty_roster(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.db"
            Database(path).replace_roster(1, [])
            with patch.dict("os.environ", {"FANTAOPERATOR_DB": str(path)}):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
                self.assertEqual(len(app.exception), 0)
                for page in app.sidebar.radio[0].options:
                    app.sidebar.radio[0].set_value(page).run()
                    self.assertEqual(len(app.exception), 0, page)

    def test_save_lineup_is_persisted_after_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.db"
            with patch.dict("os.environ", {"FANTAOPERATOR_DB": str(path)}):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
                next(b for b in app.button if "Ottimizza formazione" in b.label).click().run()
                next(b for b in app.button if b.label == "Conferma undici").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(Database(path).saved_lineup(1, 3)["players"]), 11)
                app.sidebar.radio[0].set_value("▦  Panoramica").run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any("Voti disponibili: 0/11" in c.value for c in app.caption))

    def test_anonymous_sessions_have_separate_databases(self):
        env = {k: v for k, v in os.environ.items() if k != "FANTAOPERATOR_DB"}
        with patch.dict("os.environ", env, clear=True):
            file = str(Path(__file__).resolve().parents[1] / "app.py")
            first = AppTest.from_file(file).run()
            second = AppTest.from_file(file).run()
            a = first.session_state["workspace_db"]
            b = second.session_state["workspace_db"]
            self.assertNotEqual(a.path, b.path)
            a.replace_roster(1, [])
            self.assertEqual(len(b.roster(1)), 20)
            first.run()
            self.assertEqual(first.session_state["workspace_db"].path, a.path)
            self.assertEqual(len(first.exception), 0)

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
