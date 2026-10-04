"""Loopback protocol tests use a fake server, never actual model inference."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from docreview.extract import LocalModel, demo_extract
from test_workflow import TEXT


class LocalModelTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.installed = True
        self.response = json.dumps(demo_extract(TEXT).invoice.json())
        self.done = True
        self.redirect = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.calls.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'models': [{'name': 'synthetic:fixture'}]
                                            if owner.installed else []}).encode())

            def do_POST(self):
                owner.calls.append(self.path)
                owner.payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if owner.redirect:
                    self.send_response(302)
                    self.send_header('Location', 'http://example.invalid/')
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'done': owner.done, 'response': owner.response}).encode())

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)
        self.model = LocalModel('synthetic:fixture', f'http://127.0.0.1:{self.server.server_port}')

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_protocol_and_model_review(self):
        result = self.model.extract(TEXT)
        self.assertEqual(self.calls, ['/api/tags', '/api/generate'])
        self.assertFalse(self.payload['stream'])
        self.assertFalse(self.payload['format']['additionalProperties'])
        self.assertEqual(result.provider, 'ollama:synthetic:fixture')
        self.assertIn('model_requires_review:uncalibrated extraction', result.reasons)
        self.assertIn('amount_mismatch:subtotal + tax != total', result.reasons)

    def test_absent_model_never_generates_or_pulls(self):
        self.installed = False
        with self.assertRaisesRegex(ValueError, 'not installed'):
            self.model.extract(TEXT)
        self.assertEqual(self.calls, ['/api/tags'])

    def test_malformed_output_is_reviewable(self):
        self.response = 'not JSON'
        result = self.model.extract(TEXT)
        self.assertIn('invalid_model_json', result.reasons)
        self.assertIsNone(result.invoice.total)

    def test_incomplete_response_is_flagged(self):
        self.done = False
        self.assertIn('incomplete_model_response', self.model.extract(TEXT).reasons)

    def test_remote_and_cloud_refused(self):
        for url in ['https://127.0.0.1', 'http://example.com', 'http://127.0.0.1@example.com',
                    'http://127.0.0.1/path', 'http://localhost:11434']:
            with self.assertRaises(ValueError):
                LocalModel('synthetic:fixture', url)
        with self.assertRaises(ValueError):
            LocalModel('example:cloud')

    def test_redirect_refused(self):
        self.redirect = True
        with self.assertRaisesRegex(ValueError, 'redirects'):
            self.model.extract(TEXT)


if __name__ == '__main__':
    unittest.main()
