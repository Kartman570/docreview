"""Run with python3 -m docreview; every successful command emits JSON."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .evaluate import evaluate
from .extract import LocalModel, demo_extract
from .schema import SCHEMA
from .store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / 'examples'


def read_document(path):
    with Path(path).open('rb') as source:
        data = source.read(65537)
    if not data.strip() or len(data) > 65536:
        raise ValueError('Document must contain 1–65536 UTF-8 bytes')
    return data.decode('utf-8')


def add_provider(parser):
    parser.add_argument('--provider', choices=['demo', 'ollama'], default='demo')
    parser.add_argument('--model', help='Exact installed model name from Ollama /api/tags')
    parser.add_argument('--model-url', default='http://127.0.0.1:11434')


def extractor(args):
    if args.provider == 'demo':
        if args.model:
            raise ValueError('--model requires --provider ollama')
        return demo_extract
    return LocalModel(args.model, args.model_url).extract


def demo(store):
    text = read_document(EXAMPLES / 'wrong-amount.txt')
    before = store.ingest(text, demo_extract(text))
    after = store.correct(before['document_id'], {'total': '120.00'},
                          'demo-reviewer', 'Verified synthetic subtotal 100.00 plus tax 20.00.', 1)
    return {'mode': 'deterministic demo; no model inference', 'before': before,
            'after': after, 'audit': store.audit(before['document_id'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Review synthetic invoice extractions locally.')
    parser.add_argument('--db', default='docreview.sqlite3', help='SQLite database path')
    commands = parser.add_subparsers(dest='command', required=True)
    ingest = commands.add_parser('ingest', help='Extract a synthetic UTF-8 invoice')
    ingest.add_argument('file')
    add_provider(ingest)
    commands.add_parser('queue', help='List documents awaiting review')
    for name in ('show', 'audit'):
        command = commands.add_parser(name)
        command.add_argument('id')
    correction = commands.add_parser('correct', help='Correct fields and finish human review')
    correction.add_argument('id')
    correction.add_argument('--patch', required=True, help='JSON object of corrected fields')
    correction.add_argument('--reviewer', required=True)
    correction.add_argument('--note', required=True)
    correction.add_argument('--revision', type=int, required=True)
    metric = commands.add_parser('evaluate', help='Measure raw extraction accuracy and review rate')
    metric.add_argument('--corpus', default=str(EXAMPLES / 'corpus.json'))
    add_provider(metric)
    commands.add_parser('demo', help='Flag, correct and audit a synthetic wrong total')
    commands.add_parser('schema', help='Print the extraction JSON schema')
    args = parser.parse_args(argv)
    try:
        if args.command == 'schema':
            result = SCHEMA
        elif args.command == 'evaluate':
            result = evaluate(args.corpus, extractor(args))
        elif args.command == 'ingest':
            text = read_document(args.file)
            extraction = extractor(args)(text)
            result = Store(args.db).ingest(text, extraction)
        else:
            store = Store(args.db)
            if args.command == 'demo':
                result = demo(store)
            elif args.command == 'correct':
                result = store.correct(args.id, json.loads(args.patch), args.reviewer,
                                       args.note, args.revision)
            elif args.command == 'queue':
                result = store.queue()
            else:
                result = getattr(store, args.command)(args.id)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
