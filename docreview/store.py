"""Transactional event-sourced review storage with append-only audit records."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import uuid

from .schema import FIELDS, Invoice


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.path = str(path)
        with closing(self.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS events (
                    document_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    PRIMARY KEY (document_id, revision)
                );
                CREATE TRIGGER IF NOT EXISTS no_event_update BEFORE UPDATE ON events
                BEGIN SELECT RAISE(ABORT, 'Audit events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS no_event_delete BEFORE DELETE ON events
                BEGIN SELECT RAISE(ABORT, 'Audit events are immutable'); END;
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def _events(self, db, document_id):
        rows = db.execute('SELECT * FROM events WHERE document_id=? ORDER BY revision',
                          (document_id,)).fetchall()
        if not rows:
            raise ValueError('Unknown document: ' + document_id)
        previous = ''
        events = []
        for revision, row in enumerate(rows, 1):
            payload = json.loads(row['payload'])
            if (row['revision'] != revision or row['previous_hash'] != previous
                    or digest(previous + row['payload']) != row['event_hash']
                    or payload['document_id'] != document_id or payload['revision'] != revision):
                raise ValueError('Audit integrity check failed')
            events.append({**payload, 'previous_hash': previous, 'event_hash': row['event_hash']})
            previous = row['event_hash']
        return events

    def _append(self, db, document_id, revision, action, actor, note, before, after, previous):
        payload = encoded({
            'document_id': document_id, 'revision': revision, 'action': action,
            'at': datetime.now(timezone.utc).isoformat(), 'actor': actor, 'note': note,
            'before': before, 'after': after,
        })
        db.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?)',
                   (document_id, revision, payload, previous, digest(previous + payload)))

    def ingest(self, text, extraction):
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 65536:
            raise ValueError('Document must contain 1–65536 UTF-8 bytes')
        document_id = str(uuid.uuid4())
        reasons = sorted(set(extraction.reasons + extraction.invoice.reasons()))
        after = {'fields': extraction.invoice.json(), 'reasons': reasons,
                 'status': 'needs_review' if reasons else 'accepted',
                 'provider': extraction.provider, 'raw_extraction': extraction.raw,
                 'source_text': text, 'source_sha256': digest(text)}
        with closing(self.connect()) as db, db:
            self._append(db, document_id, 1, 'extracted', 'extractor', '', None, after, '')
        return self.show(document_id)

    def audit(self, document_id):
        with closing(self.connect()) as db:
            return self._events(db, document_id)

    def show(self, document_id):
        event = self.audit(document_id)[-1]
        return {'document_id': document_id, 'revision': event['revision'], **event['after']}

    def queue(self):
        with closing(self.connect()) as db:
            ids = [r[0] for r in db.execute('SELECT DISTINCT document_id FROM events ORDER BY document_id')]
        records = [self.show(document_id) for document_id in ids]
        return [record for record in records if record['status'] == 'needs_review']

    def correct(self, document_id, patch, actor, note, expected_revision):
        if not isinstance(patch, dict) or set(patch) - set(FIELDS):
            raise ValueError('Correction must be an object containing only invoice fields')
        if not isinstance(actor, str) or not actor.strip() or not isinstance(note, str) or not note.strip():
            raise ValueError('Reviewer and correction note are required')
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            last = self._events(db, document_id)[-1]
            if type(expected_revision) is not int or last['revision'] != expected_revision:
                raise ValueError('Revision conflict: reload the document before correcting')
            before = last['after']
            if before['status'] != 'needs_review':
                raise ValueError('Document is not awaiting review')
            invoice = Invoice.parse({**before['fields'], **patch})
            if invoice.reasons():
                raise ValueError('Correction still needs review: ' + '; '.join(invoice.reasons()))
            after = {**before, 'fields': invoice.json(), 'reasons': [], 'status': 'reviewed'}
            self._append(db, document_id, last['revision'] + 1, 'reviewed', actor.strip(),
                         note.strip(), before, after, last['event_hash'])
        return self.show(document_id)
