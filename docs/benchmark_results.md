# Performance measurement

Measured with `python scripts/benchmark.py --rows 100000 --repeat 3` (107,200 input lines, 4 files, median of 3 runs,
fresh database each run, single process, Python 3.12, a small shared cloud sandbox). Your numbers will differ; the script
is the reproducible part.

| stage | before optimization | after optimization |
|---|---|---|
| ingest (parse + validate + load), median | 5.06 s (21,182 lines/s) | 4.82 s (22,255 lines/s) |
| summaries + reconciliation, median | 0.35 s | 0.31 s |
| total, median | 5.42 s | 5.13 s |

Note on noise: a later re-run in the same shared sandbox, after adding more input formats and two columns, measured only 15,000-17,000 lines/s. A profile showed unchanged functions (for example date formatting) were equally slower, so that difference is machine load, not code. Compare numbers only within one session on one machine.

Profiling (`cProfile`, 20k rows) showed the time is spread over per-line parsing/validation (about 60%) and SQLite
statements (about 13%), so there is no single large hot spot.

## Optimization 1 (kept in the code): one pass over the input keys in `canonicalize`
The first version looped over every possible alias name for every field (about 30 lookups and 14 helper calls per line).
It now does one dictionary lookup per key actually present. Gain: about 5% in this benchmark. Small, but it is free and
behaviour is unchanged (all tests still pass).

## Optimization 2 (the design choice with the biggest effect): one transaction per file
Committing after every row instead (`Config(commit_every=1)`, benchmark baseline only) on 10,000 rows:

| commit strategy | ingest throughput |
|---|---|
| commit every row | 11,400 lines/s |
| commit every 100 rows | 17,500 lines/s |
| one transaction per file (default) | 21,800 lines/s |

So the default is about 1.9x faster than the naive approach, and it is also what makes a failed file atomic.
WAL journal mode with `synchronous=NORMAL` is used for the same reason.

## What I would do next if more speed were needed
Parse files in parallel with `multiprocessing` (parsing and validation dominate and are CPU-bound), and write each
file's accepted rows with `executemany` in batches after de-duplicating by `event_id` in memory.
