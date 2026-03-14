from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATH = REPO_ROOT / "things"


class WrapperSmokeTests(unittest.TestCase):
    def test_things_wrapper_runs_cli_help(self) -> None:
        result = subprocess.run(
            [str(WRAPPER_PATH), "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Things 3 helpers via things-mcp", result.stdout)
        self.assertIn("create-task", result.stdout)


if __name__ == "__main__":
    unittest.main()