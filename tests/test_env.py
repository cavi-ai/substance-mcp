import os
import unittest
from unittest.mock import patch

from env import DESIGNER, SAMPLER, patched_environment, scrubbed_environment


class EnvironmentHelperTests(unittest.TestCase):
    def test_drops_prefixes_and_names_then_adds(self):
        with patch.dict(os.environ, {"CAVI_SAMPLER_BIN": "a", "SAMPLER_SCRIPT_PATH": "b",
                                     "CAVI_DESIGNER_APP": "c", "GONE": "d", "KEEP": "e"}):
            result = scrubbed_environment(SAMPLER, {"NEW": "f", "KEEP": "g"}, drop=("GONE",))
        self.assertNotIn("CAVI_SAMPLER_BIN", result)
        self.assertNotIn("SAMPLER_SCRIPT_PATH", result)
        self.assertNotIn("GONE", result)
        self.assertEqual((result["CAVI_DESIGNER_APP"], result["NEW"], result["KEEP"]),
                         ("c", "f", "g"))

    def test_nothing_is_dropped_by_default(self):
        with patch.dict(os.environ, {"ANYTHING": "x"}):
            self.assertEqual(scrubbed_environment(), dict(os.environ))

    def test_the_patch_replaces_the_environment_and_restores_it(self):
        with patch.dict(os.environ, {"CAVI_DESIGNER_APP": "c", "KEEP": "e"}):
            with patched_environment(DESIGNER, {"CAVI_DESIGNER_BIN_DIR": "/bin"}):
                self.assertNotIn("CAVI_DESIGNER_APP", os.environ)
                self.assertEqual(os.environ["CAVI_DESIGNER_BIN_DIR"], "/bin")
                self.assertEqual(os.environ["KEEP"], "e")
                os.environ["LEAK"] = "x"
            self.assertEqual(os.environ["CAVI_DESIGNER_APP"], "c")
            self.assertNotIn("CAVI_DESIGNER_BIN_DIR", os.environ)
            self.assertNotIn("LEAK", os.environ)


if __name__ == "__main__":
    unittest.main()
