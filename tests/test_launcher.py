import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import launcher


class LauncherTests(unittest.TestCase):
    def test_reuse_only_matching_healthy_process(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "streamlit.json"
            state.write_text(json.dumps({"pid": 123, "port": 8501}))
            command = f"python -m streamlit run {launcher.ROOT / 'app.py'} --server.port 8501"
            with patch("launcher.subprocess.run", return_value=subprocess.CompletedProcess([], 0, command)), patch("launcher.healthy", return_value=True):
                self.assertEqual(launcher.running_port(state), 8501)
            with patch("launcher.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "another-service")), patch("launcher.healthy", return_value=True):
                self.assertIsNone(launcher.running_port(state))
            with patch("launcher.subprocess.run", return_value=subprocess.CompletedProcess([], 0, command)), patch("launcher.healthy", return_value=False):
                self.assertIsNone(launcher.running_port(state))

    def test_missing_or_invalid_state_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "streamlit.json"
            self.assertIsNone(launcher.running_port(state))
            for data in ("invalid", "{}", '{"pid":0,"port":8501}', '{"pid":123,"port":80}'):
                state.write_text(data)
                self.assertIsNone(launcher.running_port(state))

    def test_no_browser_mode(self):
        with patch("launcher.subprocess.run") as run:
            launcher.show(8501, True)
        run.assert_not_called()

    def test_opens_only_local_url(self):
        with patch("launcher.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            launcher.show(8502, False)
        run.assert_called_once_with(["/usr/bin/open", "http://localhost:8502"], check=False)


if __name__ == "__main__":
    unittest.main()
