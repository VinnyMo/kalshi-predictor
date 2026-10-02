"""Offline fresh-checkout checks; no real credentials, API calls, or trades."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
NETWORK_GUARD = """
import sys
from unittest.mock import patch

network_attempts = []
def deny_network(*args, **kwargs):
    network_attempts.append(True)
    raise AssertionError("Network access is forbidden in startup tests")

try:
    with patch("requests.sessions.Session.request", side_effect=deny_network), \\
         patch("socket.socket.connect", side_effect=deny_network), \\
         patch("socket.socket.connect_ex", side_effect=deny_network):
        exec(sys.argv[1])
finally:
    assert not network_attempts, "Startup attempted network access"
"""


class StartupTests(unittest.TestCase):
    def run_isolated(self, code, *, env_text=None, overrides=None, stdin=""):
        """Run only a fresh source copy, never the developer's .env or data."""
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            for source in ROOT.glob("*.py"):
                shutil.copy2(source, checkout / source.name)
            # Even the missing-setting case gets an empty file so dotenv cannot
            # search a parent directory for unrelated local credentials.
            (checkout / ".env").write_text(env_text or "", encoding="utf-8")
            # Do not inherit API keys, PYTHONPATH, or any other local settings.
            environment = {
                key: os.environ[key]
                for key in ("PATH", "SystemRoot")
                if key in os.environ
            }
            environment.update({"NO_COLOR": "1", "COLUMNS": "120"})
            environment.update(overrides or {})
            return subprocess.run(
                [sys.executable, "-c", NETWORK_GUARD, code],
                cwd=checkout,
                env=environment,
                input=stdin,
                text=True,
                capture_output=True,
                timeout=15,
            )

    def config_result(self, **kwargs):
        result = self.run_isolated(
            "from config import config; import json; "
            "print(json.dumps([config.starting_balance, config.environment, config.validate()]))",
            **kwargs,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_example_has_explicit_nonsecret_balance(self):
        from dotenv import dotenv_values

        example = dotenv_values(ROOT / ".env.example")
        self.assertEqual(example["STARTING_BALANCE"], "100")
        self.assertEqual(example["KALSHI_ENVIRONMENT"], "demo")
        self.assertEqual(example["KALSHI_API_KEY_ID"], "")
        self.assertEqual(example["KALSHI_PRIVATE_KEY"], "")

    def test_copied_example_loads_and_explains_missing_credentials(self):
        balance, environment, (valid, errors) = self.config_result(
            env_text=(ROOT / ".env.example").read_text(encoding="utf-8")
        )
        self.assertEqual(balance, 100.0)
        self.assertEqual(environment, "demo")
        self.assertFalse(valid)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("KALSHI_API_KEY_ID" in error for error in errors))
        self.assertTrue(any("KALSHI_PRIVATE_KEY" in error for error in errors))

    def test_missing_balance_uses_default(self):
        self.assertEqual(self.config_result()[0], 100.0)

    def test_blank_balance_uses_default(self):
        self.assertEqual(self.config_result(env_text="STARTING_BALANCE=\n")[0], 100.0)

    def test_whitespace_balance_uses_default(self):
        self.assertEqual(
            self.config_result(overrides={"STARTING_BALANCE": "  "})[0], 100.0
        )

    def test_explicit_balance_is_preserved(self):
        self.assertEqual(
            self.config_result(overrides={"STARTING_BALANCE": "250.50"})[0], 250.5
        )

    def test_zero_balance_is_preserved(self):
        self.assertEqual(self.config_result(overrides={"STARTING_BALANCE": "0"})[0], 0)

    def test_blank_credentials_exit_with_guidance_without_traceback(self):
        result = self.run_isolated(
            "import runpy; runpy.run_path('main.py', run_name='__main__')",
            env_text=(ROOT / ".env.example").read_text(encoding="utf-8"),
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Configuration Error", result.stdout)
        self.assertIn("PEM contents (not a file path)", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_menu_opens_and_exits_without_network(self):
        result = self.run_isolated(
            "import runpy; runpy.run_path('main.py', run_name='__main__')",
            env_text=(ROOT / ".env.example").read_text(encoding="utf-8"),
            overrides={
                "KALSHI_API_KEY_ID": "offline-test-only",
                # Just the marker accepted by config validation, not key material.
                "KALSHI_PRIVATE_KEY": "offline PRIVATE KEY placeholder",
            },
            stdin="0\n",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Main Menu", result.stdout)
        self.assertIn("$100.00", result.stdout)
        self.assertIn("Goodbye!", result.stdout)


if __name__ == "__main__":
    unittest.main()
