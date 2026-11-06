"""Native acceptance cannot turn skipped or failed tests into passing evidence."""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from native_workspace import native_workspace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))

from native import NativeResult


class NativeOutcomeTests(unittest.TestCase):
    def execute(self, case):
        return unittest.TextTestRunner(stream=io.StringIO(), resultclass=NativeResult).run(case)

    def test_skipped_native_test_is_a_failed_acceptance_outcome(self):
        class Skipped(unittest.TestCase):
            @unittest.skip('application unavailable')
            def runTest(self):
                pass

        result = self.execute(Skipped())
        self.assertEqual(len(result.outcomes), 1)
        self.assertFalse(result.outcomes[0]['passed'])
        self.assertIn('application unavailable', result.outcomes[0]['reason'])

    def test_failed_native_test_is_not_reported_as_passing(self):
        class Failed(unittest.TestCase):
            def runTest(self):
                self.fail('expected rejection')

        result = self.execute(Failed())
        self.assertFalse(result.outcomes[0]['passed'])
        self.assertEqual(len(result.failures), 1)

    def test_failed_workspace_stays_at_its_original_evidence_path(self):
        with tempfile.TemporaryDirectory() as evidence, \
                patch.dict(os.environ, {'CAVI_NATIVE_EVIDENCE_DIR': evidence}):
            with self.assertRaisesRegex(ValueError, 'native failure'), native_workspace() as folder:
                fixture = Path(folder) / 'source.sbs'
                fixture.write_text('fixture')
                raise ValueError('native failure')
            self.assertEqual(fixture.read_text(), 'fixture')

    def test_successful_native_workspace_is_removed(self):
        with tempfile.TemporaryDirectory() as evidence, \
                patch.dict(os.environ, {'CAVI_NATIVE_EVIDENCE_DIR': evidence}):
            with native_workspace() as folder:
                fixture = Path(folder) / 'source.sbs'
                fixture.write_text('fixture')
            self.assertFalse(Path(folder).exists())
