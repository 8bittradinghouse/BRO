# Timing And WS-Source Diagnostic Report (2026-05-09)

## Scope
This is a report-only diagnostic artifact for two investigated residual patients:
- host timing discipline / jitter policy failure
- taker `taker_requires_ws_book_source` late-window blocks

It preserves:
- what the issues looked like,
- what we did to investigate them,
- what the live and postrun evidence actually showed,
- and what low-blast actions make sense if the pattern returns later.

This report does **not** authorize a runtime behavior change by itself.

## Why This Exists
The concern was that these two signals might share one upstream disease:
- global timing drift,
- host slowdown,
- websocket timing degradation,
- or a taker-to-websocket short-circuit under pressure.

The goal of this report is to preserve a clean truth anchor in case the pattern resurfaces under a different regime.

## Questions Investigated
1. Is `max_clock_jitter_ms=10.0` realistic on this host for canonical paper validation?
2. Is `taker_requires_ws_book_source` a chronic taker disease, a websocket transport problem, or a slice-specific source-ownership issue?
3. Are the two signals one shared root, or two separate patients?

## Investigation Work Performed
### Artifact and code-path tracing
- `VERIFIED`: timing owner chain traced through:
  - `configs/profiles/execution_defaults.yaml`
  - `prodesk/preflight.py`
  - `prodesk/time_sync.py`
  - `scripts/canonical_paper_session.py`
  - `scripts/time_discipline_audit.py`
- `VERIFIED`: WS-source owner chain traced through:
  - `prodesk/book_feed.py`
  - `prodesk/market_data.py`
  - `prodesk/models.py`
  - `executor.py`
  - `prodesk/edge_truth_contract.py`

### Historical comparison
- `VERIFIED`: failing timing specimen:
  - run `656b8108-f155-4493-807b-3897034fcd4f`
- `VERIFIED`: clean comparator timing specimens:
  - run `4b60bf3e-63c9-4fb0-a47d-69cfb76216d0`
  - run `195ac6ea-367d-4eff-9561-eb6539896c1c`
- `VERIFIED`: mixed WS-source historical specimens reviewed:
  - run `656b8108-f155-4493-807b-3897034fcd4f`
  - run `cd42bf13-b226-4c2e-b5a0-26e021522bda`
  - run `248a0183-f15d-49c9-96d8-f5f9851e1607`

### Live host inspection
- `VERIFIED`: live `timedatectl timesync-status` during investigation showed:
  - `Server=2.time.constant.com`
  - `Jitter=5.499ms`
  - `Offset=+1.709ms`
  - `PollInterval=34min 8s`

### Direct host inspection commands used
- `VERIFIED`: the investigation included direct host timing inspection with:
  - `timedatectl status`
  - `timedatectl timesync-status`
  - `timedatectl show-timesync --all`
  - `systemctl status systemd-timesyncd --no-pager -l`
- `VERIFIED`: current host config inspection showed:
  - `/etc/systemd/timesyncd.conf` explicitly pins:
    - `1.time.constant.com`
    - `2.time.constant.com`
    - `3.time.constant.com`
  - no active `timesyncd` drop-in override was present at investigation time
- `VERIFIED`: no host hardening was applied during this packet because passwordless `sudo` was unavailable in-session

### Fresh watched specimens
- `VERIFIED`: two fresh cold-start `10` minute watched runs were executed end to end:
  - `0662bc07-97e6-4ec9-baf9-faea9fdfee9c`
  - `a8f2d568-dfa3-45fd-a9d7-85917c41ed39`
- `VERIFIED`: both finished:
  - `VALID_ACTIVE`
  - `overall_exit_code=0`
  - `time_discipline_audit=0`
  - `websocket_hardening_audit=0`

## Things Already Done
- `VERIFIED`: we did not stop at one suspicious specimen; we widened the evidence set with:
  - one failing timing specimen
  - two older clean timing comparators
  - three older mixed WS-source specimens
  - two fresh cold-start watched `10` minute specimens
- `VERIFIED`: we investigated from both directions:
  - postrun reports and audits
  - live host timing state
  - runtime event tape and taker decision rows
  - code-owner tracing from config to validator to runtime to report
- `VERIFIED`: we deliberately held the runtime line during diagnosis:
  - no taker source-doctrine loosening
  - no jitter-threshold widening
  - no runtime code patch based only on the earlier ugly slice
- `VERIFIED`: we also identified one optional low-blast host hardening path for future use, but did not apply it yet:
  - `systemd-timesyncd` poll tightening via `PollIntervalMaxSec=512`

## Issue 1: Host Timing Discipline / Jitter
### Observed failing specimen
- `VERIFIED`: run `656b8108-f155-4493-807b-3897034fcd4f` failed `time_discipline_audit` only because host jitter exceeded the threshold.
- Evidence:
  - [time_discipline_audit.json](/home/odah/bro/base/logs_exec/paper_universal/reports/656b8108-f155-4493-807b-3897034fcd4f/time_discipline_audit.json:50)
  - [host_time_sync_active_start.json](/home/odah/bro/base/logs_exec/paper_universal/sessions/499f63fb-c670-4dff-b670-11cced6d35a0/reports/host_time_sync_active_start.json:1)
- Exact failing host sample:
  - `jitter_ms=11.761`
  - `offset_ms=-0.181`
  - `root_distance_ms=38.703`
  - `server=2.time.constant.com`
  - `poll_interval=34min 8s`

### Counterevidence
- `VERIFIED`: `10ms` is attainable on this same host.
- Clean timing anchors:
  - `4b60...`: `jitter_ms=2.836`, `server=1.time.constant.com`
  - `195a...`: `jitter_ms=5.058`, `server=2.time.constant.com`
  - `0662...`: `jitter_ms=5.499`, `server=2.time.constant.com`
  - `a8f2...`: `jitter_ms=5.499` at start, later `5.775`, `server=2.time.constant.com`
- Evidence:
  - [host_time_sync_active_start.json](/home/odah/bro/base/logs_exec/paper_universal/sessions/d8360bec-8e02-46dd-88aa-3c599f0d784f/reports/host_time_sync_active_start.json:1)
  - [host_time_sync_active_start.json](/home/odah/bro/base/logs_exec/paper_universal/sessions/6fd542ef-c34a-4a70-a429-0643e9446e19/reports/host_time_sync_active_start.json:1)
  - [host_time_sync_active_start.json](/home/odah/bro/base/logs_exec/paper_universal/sessions/5bd1168f-0da9-4088-801a-3699d7185a01/reports/host_time_sync_active_start.json:1)
  - [host_time_sync_active_start.json](/home/odah/bro/base/logs_exec/paper_universal/sessions/73ea9709-26ad-4d5a-8694-520039efbfe2/reports/host_time_sync_active_start.json:1)

### What the fresh runs changed
- `VERIFIED`: the fresh watched pair did **not** reproduce the timing failure.
- `VERIFIED`: both passed with clean time-discipline audits:
  - [time_discipline_audit.json](/home/odah/bro/base/logs_exec/paper_universal/reports/0662bc07-97e6-4ec9-baf9-faea9fdfee9c/time_discipline_audit.json:1)
  - [time_discipline_audit.json](/home/odah/bro/base/logs_exec/paper_universal/reports/a8f2d568-dfa3-45fd-a9d7-85917c41ed39/time_discipline_audit.json:1)
- `INFERRED`: the earlier jitter miss now looks more like a bad host-sync slice than proof that the doctrinal threshold is unrealistic.

### Current diagnosis
- `VERIFIED`: this is a host-policy patient, not an oracle-feed timing-chain patient.
- `VERIFIED`: no event-domain skew failure was present in the failing specimen.
- `INFERRED`: the most likely root is a transient host-side sync quality wobble while running with a relatively long `timesyncd` poll interval.
- `INFERRED`: the current evidence does **not** support the story that incoming oracle cadence itself forced the host beyond the `10ms` doctrine line.

## Issue 2: Taker WS-Source Gate
### Historical ugly slices
- `VERIFIED`: `taker_requires_ws_book_source` appeared in older watched runs, including:
  - run `656b...`: `11` rows
  - run `cd42...`: `24` rows
  - run `248a...`: `42` rows

### Important nuance from bedrock review
- `VERIFIED`: these rows did **not** prove websocket transport failure.
- `VERIFIED`: websocket audit stayed clean in the investigated specimens.
- `VERIFIED`: the taker WS gate is a source-ownership fail-close, not a host-jitter fail-close.
- `VERIFIED`: repeated `held_valuation_rest_fallback_applied` events were observed in the same families of runs.

### Historical live-sequence findings
- `VERIFIED`: in run `656b...`, taker already got a real shot off on WS, and the later `taker_requires_ws_book_source` rows were on a different ref after:
  - `10` `complement_token_mapping_unavailable` WS rows
  - repeated REST fallback replacement events
- `VERIFIED`: in run `248a...`, the `42` WS-source rows were on different refs than the actual taker shot that later submitted and filled cleanly on WS.
- `VERIFIED`: in run `cd42...`, the same refs moved between:
  - `rest/missing`
  - then `ws/bounded_single_side_touch`
  - then back to `rest/missing`
- `INFERRED`: that pattern fits slice-specific source-ownership ugliness much better than a dead websocket.

### What the fresh runs changed
- `VERIFIED`: neither fresh watched specimen produced `taker_requires_ws_book_source`.
- Fresh run `0662...`:
  - no taker submits
  - taker blockers were only:
    - `edge_below_min`
    - `token_score_below_taker_min`
- Fresh run `a8f2...`:
  - `2` taker submits
  - `2` taker fills
  - taker blockers were:
    - `edge_below_min`
    - `taker_visible_fill_ratio_below_min`
    - `token_score_below_taker_min`
    - `complement_token_mapping_unavailable`
- Evidence:
  - [nightly_soak_report.txt](/home/odah/bro/base/logs_exec/paper_universal/reports/0662bc07-97e6-4ec9-baf9-faea9fdfee9c/nightly_soak_report.txt:1)
  - [nightly_soak_report.txt](/home/odah/bro/base/logs_exec/paper_universal/reports/a8f2d568-dfa3-45fd-a9d7-85917c41ed39/nightly_soak_report.txt:1)

### Current diagnosis
- `VERIFIED`: no fresh proof of a chronic WS-source taker disease was reproduced.
- `VERIFIED`: the fresh pair showed clean taker reads on:
  - `ws + bounded_single_side_touch`
  - `ws + direct_midpoint`
- `INFERRED`: the earlier WS-source clusters should currently be treated as recurrence-watch slice pathology, not proven root lane failure.
- `INFERRED`: this leaves open two weaker possibilities that are worth remembering without overreacting to them:
  - a one-off bad live section
  - a regime-specific interaction between held-valuation fallback and the active taker target lane

## Shared-Root Assessment
- `VERIFIED`: the fresh pair did **not** support a single shared root.
- `VERIFIED`: timing passed cleanly while taker behavior varied normally by edge/liquidity conditions.
- `VERIFIED`: WS-source failure did not recur under clean host timing in the fresh watched pair.
- `INFERRED`: the stronger current model is:
  - timing issue = host-sync slice / host-policy patient
  - WS-source issue = slice-specific source-ownership / valuation-fallback ugliness when it appears

## No-Build Conclusions
### Hold
- `VERIFIED`: keep `max_clock_jitter_ms=10.0` for now.
- `VERIFIED`: keep WS-only taker source doctrine for now.
- `VERIFIED`: do not loosen taker to accept REST from this evidence set.

### What we intentionally did not do
- `VERIFIED`: we did not widen BRO timing policy just because one host sample missed.
- `VERIFIED`: we did not loosen taker to accept REST-backed market truth.
- `VERIFIED`: we did not open a runtime surgery packet on websocket ownership from this evidence alone.
- `VERIFIED`: we did not classify the earlier WS-source cluster as chronic lane disease after the fresh watched pair failed to reproduce it.

### Optional host hardening if the timing slice recurs
- `INFERRED`: the lowest-blast hardening move is host-side `systemd-timesyncd` poll tightening, not BRO timing-policy widening.
- Candidate drop-in:

```ini
[Time]
PollIntervalMaxSec=512
```

- Candidate commands:

```bash
sudo install -d -m 0755 /etc/systemd/timesyncd.conf.d
sudo tee /etc/systemd/timesyncd.conf.d/10-bro-tighten.conf >/dev/null <<'EOF'
[Time]
PollIntervalMaxSec=512
EOF
sudo systemctl restart systemd-timesyncd
timedatectl timesync-status
```

## Future Recurrence Triggers
If this theme comes back later, check in this order:

1. Host timing
- `timedatectl timesync-status`
- active-start and active-sample host timing artifacts
- whether jitter is actually above `10ms`

2. WS transport health
- `websocket_hardening_audit.json`
- reconnect count
- last-message age
- ordering failures

3. Taker source ownership
- whether the blocked taker ref is the real shot lane or a different follow-on ref
- whether `held_valuation_rest_fallback_applied` happened before the WS-source block cluster
- whether the same target ref later recovers to clean `ws` and continues evaluating

4. Real blocker classification
- same-target `taker_requires_ws_book_source` before any viable WS taker read = stronger concern
- same-target `edge_below_min` / `visible_fill_ratio` / `token_score` on WS with later submit/fill = healthy competitive filtering, not source disease

## Regime-Reentry Note
- `INFERRED`: if this returns during a clearly different market or host regime, the right first question is not "was the old diagnosis wrong?"
- `INFERRED`: the right first question is:
  - did host timing posture change,
  - did websocket transport health change,
  - or did the same target-lane source-ownership pattern return under a different live slice?
- `VERIFIED`: this report is meant to preserve that distinction so a future recurrence does not immediately get overfit into one broad "global timing made the host slow" story.

## Current Status
- `VERIFIED`: both fresh watched specimens were healthy enough to materially downgrade the fear that host jitter and taker WS-source were one shared chronic disease.
- `VERIFIED`: this report should be treated as the diagnostic anchor if the pattern resurfaces under a new market or timing regime.
