"""The production publisher must never echo raw subprocess failures to Actions logs."""
from __future__ import annotations

import io
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pipeline_common import PipelineError
from publication_pipeline import run_script


class SafePublicationDiagnosticsTests(unittest.TestCase):
    def failed(self, stage, stdout=b'', stderr=b''):
        result = subprocess.CompletedProcess([], 1, stdout=stdout, stderr=stderr)
        output = io.StringIO()
        with patch('publication_pipeline.subprocess.run', return_value=result):
            with redirect_stdout(output), self.assertRaisesRegex(
                    PipelineError, '^BUILD_OR_VALIDATION_FAILED$'):
                run_script(Path('/not-a-real-repository'), stage)
        return output.getvalue()

    def test_validate_failure_logs_only_allowlisted_code(self):
        message = self.failed(
            'validate.py',
            stdout=b'VALIDATION ERROR: GA4_TAG_MISSING_OR_DUPLICATE\n'
                   b'URL=https://example.invalid/?token=secret-private',
            stderr=b'/sensitive/home/profile secret-private')
        self.assertEqual(
            message,
            'PUBLICATION_PREPARE_FAILED_STAGE: SITE_VALIDATE / GA4_TAG_MISSING_OR_DUPLICATE\n')
        self.assertNotIn('secret-private', message)
        self.assertNotIn('example.invalid', message)

    def test_unstructured_build_failure_logs_fixed_stage_only(self):
        message = self.failed('build_site.py', stderr=b'Traceback /private/key=secret-private')
        self.assertEqual(
            message, 'PUBLICATION_PREPARE_FAILED_STAGE: SITE_BUILD / UNCLASSIFIED\n')

    def test_untrusted_validation_text_is_not_logged(self):
        message = self.failed('validate.py',
            stdout=b'VALIDATION ERROR: fixture-private-token cookie=secret-private')
        self.assertEqual(
            message, 'PUBLICATION_PREPARE_FAILED_STAGE: SITE_VALIDATE / UNCLASSIFIED\n')
        self.assertNotIn('secret-private', message)

    def test_unknown_script_does_not_log_script_name(self):
        message = self.failed('unknown-secret-private.py')
        self.assertEqual(
            message, 'PUBLICATION_PREPARE_FAILED_STAGE: OTHER / UNCLASSIFIED\n')

    def test_success_has_no_diagnostics(self):
        result = subprocess.CompletedProcess([], 0, stdout=b'ok', stderr=b'')
        output = io.StringIO()
        with patch('publication_pipeline.subprocess.run', return_value=result), redirect_stdout(output):
            run_script(Path('/not-a-real-repository'), 'build_site.py')
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
