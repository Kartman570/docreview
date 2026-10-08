import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from docreview.extract import demo_extract
from docreview.schema import Invoice
from docreview.store import Store

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / 'examples/wrong-amount.txt').read_text()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'review.sqlite3'
        self.store = Store(self.path)

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, '-m', 'docreview', '--db', str(self.path), *args],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stderr)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def ingest(self):
        return self.store.ingest(TEXT, demo_extract(TEXT))['document_id']

    def test_cli_full_review_and_restart(self):
        record = self.cli('ingest', 'examples/wrong-amount.txt')
        doc = record['document_id']
        self.assertEqual(record['status'], 'needs_review')
        self.assertIn('amount_mismatch:subtotal + tax != total', record['reasons'])
        self.assertEqual(self.cli('queue')[0]['document_id'], doc)
        result = self.cli('correct', doc, '--patch', '{"total":"120.00"}',
                          '--reviewer', 'reviewer-a', '--note', 'Checked synthetic amounts', '--revision', '1')
        self.assertEqual(result['status'], 'reviewed')
        self.assertEqual(self.cli('show', doc)['fields']['total'], '120.00')
        self.assertEqual(self.cli('queue'), [])
        audit = self.cli('audit', doc)
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[1]['before']['fields']['total'], '129.00')
        self.assertEqual(audit[1]['after']['fields']['total'], '120.00')
        self.assertEqual(audit[1]['actor'], 'reviewer-a')
        self.assertEqual(audit[0]['after']['source_text'], TEXT)
        self.assertEqual(audit[1]['previous_hash'], audit[0]['event_hash'])

    def test_cli_demo_repeatable(self):
        first, second = self.cli('demo'), self.cli('demo')
        self.assertNotEqual(first['after']['document_id'], second['after']['document_id'])
        self.assertEqual(first['after']['fields']['total'], '120.00')
        self.assertEqual(len(self.cli('audit', first['after']['document_id'])), 2)

    def test_cli_metrics(self):
        result = self.cli('evaluate')
        self.assertEqual(result['samples'], 10)
        self.assertEqual(result['review_count'], 7)
        self.assertEqual(result['per_field']['total']['correct'], 7)
        self.assertEqual(result['per_field']['vendor']['correct'], 8)
        self.assertEqual(result['providers'], ['deterministic-demo-v1'])

    def test_invalid_correction_rolls_back(self):
        doc = self.ingest()
        for patch in ({'total': '121.00'}, {'total': 120}, {'total': None}, {'unexpected': 'x'}):
            with self.assertRaises(ValueError):
                self.store.correct(doc, patch, 'reviewer', 'Checked', 1)
        self.assertEqual(len(self.store.audit(doc)), 1)

    def test_stale_revision_and_repeated_review(self):
        doc = self.ingest()
        with self.assertRaisesRegex(ValueError, 'Revision conflict'):
            self.store.correct(doc, {'total': '120.00'}, 'reviewer', 'Checked', 0)
        self.store.correct(doc, {'total': '120.00'}, 'reviewer', 'Checked', 1)
        with self.assertRaisesRegex(ValueError, 'not awaiting'):
            self.store.correct(doc, {}, 'reviewer', 'Checked', 2)

    def test_concurrent_review_has_one_winner(self):
        doc = self.ingest()
        def review(actor):
            try:
                Store(self.path).correct(doc, {'total': '120.00'}, actor, 'Checked', 1)
                return 'ok'
            except ValueError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(review, ['a', 'b']))
        self.assertCountEqual(results, ['ok', 'conflict'])
        self.assertEqual(len(self.store.audit(doc)), 2)

    def test_reviewer_and_note_required(self):
        doc = self.ingest()
        for actor, note in [('', 'Checked'), ('reviewer', ' ')]:
            with self.assertRaises(ValueError):
                self.store.correct(doc, {'total': '120.00'}, actor, note, 1)

    def test_immutable_events_and_tamper_detection(self):
        doc = self.ingest()
        with sqlite3.connect(self.path) as db:
            for statement in ('DELETE FROM events', "UPDATE events SET payload='{}'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
            db.execute('DROP TRIGGER no_event_update')
            db.execute("UPDATE events SET previous_hash='tampered'")
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.store.audit(doc)

    def test_duplicate_label_requires_explicit_review(self):
        text = TEXT.replace('129.00', '120.00') + 'total: 120.00\n'
        doc = self.store.ingest(text, demo_extract(text))
        self.assertIn('ambiguous:total', doc['reasons'])
        final = self.store.correct(doc['document_id'], {}, 'reviewer', 'Confirmed duplicate agrees', 1)
        self.assertEqual(final['status'], 'reviewed')

    def test_unknown_document_cli_error(self):
        self.assertIn('Unknown document', self.cli('show', 'absent', code=2)['error'])

    def test_size_limit_cli(self):
        source = Path(self.temp.name) / 'large.txt'
        source.write_text('x' * 65537)
        self.assertIn('65536', self.cli('ingest', str(source), code=2)['error'])


class SchemaTests(unittest.TestCase):
    def test_decimal_arithmetic(self):
        text = TEXT.replace('100.00', '0.10').replace('20.00', '0.20').replace('129.00', '0.30')
        self.assertEqual(demo_extract(text).reasons, [])

    def test_invalid_fields_become_null_with_reasons(self):
        result = demo_extract(TEXT.replace('129.00', 'NaN').replace('2026-11-20', '2026-02-30'))
        self.assertIsNone(result.invoice.total)
        self.assertIn('invalid_field:total', result.reasons)
        self.assertIn('invalid_field:invoice_date', result.reasons)

    def test_blank_label_does_not_consume_next_line(self):
        result = demo_extract(TEXT.replace('vendor: Example Paper Company', 'vendor: '))
        self.assertIsNone(result.invoice.vendor)
        self.assertIn('missing:vendor', result.reasons)
        self.assertEqual(str(result.invoice.invoice_date), '2026-11-20')

    def test_strict_schema(self):
        base = demo_extract(TEXT).invoice.json()
        for value in (120, True, 120.0, '1e2', '-1.00', '100.001', 'Infinity'):
            with self.assertRaises(ValueError):
                Invoice.parse({**base, 'total': value})
        with self.assertRaises(ValueError):
            Invoice.parse({**base, 'extra': 'field'})
        with self.assertRaises(ValueError):
            Invoice.parse({})


if __name__ == '__main__':
    unittest.main()
