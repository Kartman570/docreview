# DocReview

A small Python backend and CLI for extracting **synthetic invoice text**, routing uncertain results to human review, and preserving correction history in SQLite. No real invoices, payment processing, OCR, cloud API, or model download is involved.

The default `demo` provider is a deterministic label parser, **not model inference**. An optional Ollama adapter invokes an already-installed local model. Provider identity is stored with every extraction and included in evaluation results.

## Quickstart

Use Python 3.10 or newer from the repository directory. No packages or network access are required for the default workflow. Validated on Python 3.14.5; other versions have not been exercised.

```sh
python3 -m docreview --help
python3 -m docreview --db demo.sqlite3 demo
python3 -m docreview evaluate
python3 -m unittest discover -s tests -v
```

The demo reads `examples/wrong-amount.txt`, extracts a total of `129.00`, and flags `amount_mismatch:subtotal + tax != total`. It then simulates a reviewer correcting the total to `120.00` after checking `100.00 + 20.00`. Output contains `before`, `after`, and two audit events. The final status is `reviewed`. Each rerun creates a new document ID, preserving earlier runs. The demo's reviewer is scripted; it is not evidence of a real human approval.

## Manual review

```sh
python3 -m docreview --db review.sqlite3 ingest examples/wrong-amount.txt
python3 -m docreview --db review.sqlite3 queue
```

Copy the returned `document_id` into `DOCUMENT_ID` below. Review the source text and explicit reasons before correcting:

```sh
python3 -m docreview --db review.sqlite3 show DOCUMENT_ID
python3 -m docreview --db review.sqlite3 correct DOCUMENT_ID \
  --patch '{"total":"120.00"}' --revision 1 \
  --reviewer local-reviewer --note 'Checked the synthetic subtotal and tax.'
python3 -m docreview --db review.sqlite3 audit DOCUMENT_ID
python3 -m docreview --db review.sqlite3 queue
```

`--db` is a global option and goes before the command. Successful commands print JSON and exit 0; operational errors print JSON to stderr and exit 2. Argument errors use argparse's standard text and exit 2. A correction must produce a complete, valid, arithmetically consistent invoice. It atomically records the reviewer, note, original values, corrected values, timestamp, and revision. Stale revisions are rejected. For a structurally valid ambiguous or model-derived extraction, `--patch '{}'` explicitly confirms the values; a reviewer and note are still required. Accepted or reviewed records cannot be reviewed again in this version.

## Schema and review policy

`python3 -m docreview schema` prints the JSON schema. Python runtime validation also checks real calendar dates and arithmetic.

| Field | Python type | JSON representation |
| --- | --- | --- |
| invoice_id, vendor | str or None | Nonblank string, at most 200 characters, or null |
| invoice_date | datetime.date or None | YYYY-MM-DD or null |
| currency | str or None | USD, EUR, GBP, or null |
| subtotal, tax, total | decimal.Decimal or None | Nonnegative decimal string with exactly two places, at most ten integer digits, or null |

All seven keys are required and unknown keys are rejected by the strict invoice parser. Extraction normalizes malformed or absent fields to null while retaining raw output and explicit reasons. Null means unknown; it never means zero. No float rounding, guessing, or automatic repair of totals is performed. Credit notes, other currencies, and line-item reconciliation are outside this schema.

The deterministic provider recognizes exact lowercase `field: value` labels, one per line. Duplicate labels keep the first candidate but trigger `ambiguous:<field>`. Missing fields, invalid values, unknown model fields, incomplete model responses, and arithmetic mismatches trigger review. Consistent complete demo extractions are `accepted`; that means only that these checks passed. Every model extraction is reviewed because model confidence is uncalibrated. No probability or accuracy claim is inferred from a model's self-reported confidence.

## Evaluation

```sh
python3 -m docreview evaluate
python3 -m docreview evaluate --corpus examples/corpus.json
```

The checked-in ten-document corpus is entirely synthetic. It covers valid USD/EUR/GBP, decimal arithmetic, a wrong amount, a missing vendor, a duplicate total, an invalid date, an OCR-like amount typo, an unsupported currency spelling, and an unrecognized label. Each sample has a separate gold `expected` object representing its intended invoice fields. For deliberately damaged or missing text, gold values are known from fixture construction; the extractor never reads those labels. This measures end-to-end field recovery, including failures it cannot recover from the visible text alone.

Measured deterministic baseline before corrections:

| Field | Correct / total | Accuracy |
| --- | --- | --- |
| invoice_id | 10 / 10 | 100% |
| vendor | 8 / 10 | 80% |
| invoice_date | 9 / 10 | 90% |
| currency | 9 / 10 | 90% |
| subtotal | 10 / 10 | 100% |
| tax | 10 / 10 | 100% |
| total | 7 / 10 | 70% |

Review rate: **7 / 10 (70%)**. Metrics are exact matches over every sample for each field, before human corrections; missing predictions count as incorrect against nonnull labels. Output includes counts, provider identities, failed fields, and review reasons per sample. This tiny curated diagnostic corpus is not a held-out model benchmark and does not estimate real invoice performance. Evaluation is read-only and does not populate the review database.

## Optional installed local model

Start your existing Ollama service with [cloud features disabled](https://docs.ollama.com/faq#how-do-i-disable-ollama-cloud-features) (`OLLAMA_NO_CLOUD=1` in its environment). Inspect its installed model list, then substitute an exact listed local model name:

```sh
curl --noproxy '*' http://127.0.0.1:11434/api/tags
python3 -m docreview --db model.sqlite3 ingest examples/wrong-amount.txt \
  --provider ollama --model YOUR_INSTALLED_MODEL
python3 -m docreview evaluate --provider ollama --model YOUR_INSTALLED_MODEL
```

DocReview uses Ollama's documented [installed model listing](https://docs.ollama.com/api/tags) and [structured generation API](https://docs.ollama.com/api/generate). It checks the exact installed name, refuses cloud names and remote-model metadata, and never calls a pull endpoint. HTTP is limited to the literal loopback address `127.0.0.1`; proxies and redirects are disabled. `--model-url http://127.0.0.1:PORT` supports another local port. Requests time out after 30 seconds, input is capped at 64 KiB, and responses at 1 MiB. Treat the locally operated service as trusted: a client cannot enforce how that service itself is configured or whether it forwards traffic.

Transport failures abort ingestion without creating a record. Malformed generated JSON becomes a reviewable all-null extraction with raw output preserved. Ollama provider labels indicate the requested inference path, not independently verified model provenance. The adapter is tested against a fake loopback HTTP service; **actual model inference was not verified**, because the available service reported zero installed models. No weights were downloaded.

## Storage and development

`docreview.schema` provides typed validation, `docreview.extract` provides extraction adapters, and `docreview.store.Store` implements the review backend. The CLI calls the same service used by tests. SQLite events are the source of truth; there is no separate mutable state table to drift out of sync. Each event stores before/after snapshots, original text and SHA-256, raw extraction, provider, reasons, actor, timestamp, and revision. A per-document hash chain is checked on reads. Transactions plus revision checks prevent two reviewers from overwriting one another.

Database triggers reject ordinary updates and deletes to audit events. This is durable local history and tamper detection, **not** a signed or externally anchored compliance log: a database administrator can drop triggers, rewrite hashes, or truncate history. Reviewer names are self-asserted, not authenticated. There is no web server, user authorization, encryption, deployment configuration, or production invoice support. Synthetic text and raw model output are stored in plaintext. Back up the SQLite file using SQLite's backup API when the application is running.

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q docreview tests
python3 -m tabnanny docreview tests
git diff --check
```

Tests exercise CLI ingestion, queueing, correction, persistent audit reads in new processes, repeatable demos, exact metrics, schema failures, blank labels, concurrent review, rollback, tampering, and the mock local-model protocol. Runtime SQLite files and Python caches are ignored. There are no third-party runtime or test dependencies.
