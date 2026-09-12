"""Exact-match extraction metrics before any reviewer corrections."""
import json
from pathlib import Path

from .schema import FIELDS, Invoice


def evaluate(path, extractor):
    corpus = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(corpus, list) or not corpus:
        raise ValueError('Corpus must be a nonempty list')
    counts = dict.fromkeys(FIELDS, 0)
    reviewed = 0
    failures = []
    providers = set()
    seen = set()
    for sample in corpus:
        if sample['id'] in seen:
            raise ValueError('Duplicate corpus sample ID')
        seen.add(sample['id'])
        expected = Invoice.parse(sample['expected']).json()
        extraction = extractor(sample['text'])
        providers.add(extraction.provider)
        actual = extraction.invoice.json()
        reviewed += bool(extraction.reasons)
        wrong = [key for key in FIELDS if actual[key] != expected[key]]
        for key in FIELDS:
            counts[key] += key not in wrong
        failures.append({'id': sample['id'], 'wrong_fields': wrong,
                         'review_reasons': extraction.reasons})
    size = len(corpus)
    return {'samples': size, 'providers': sorted(providers), 'measurement': 'before human correction',
            'per_field': {key: {'correct': value, 'total': size, 'accuracy': value / size}
                          for key, value in counts.items()},
            'review_count': reviewed, 'review_rate': reviewed / size, 'details': failures}
