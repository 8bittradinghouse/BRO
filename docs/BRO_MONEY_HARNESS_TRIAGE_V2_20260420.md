# BRO Money Harness Triage v2 (2026-04-20)

Triage rule: `ORANGE_CANDIDATE if tags include broad_exception OR suppressed_exception OR subprocess_without_timeout; else YELLOW`

Current disposition note (2026-04-21):
- This v2 board is a historical intake snapshot.
- Authoritative yellow disposition for the current tree is in:
  - `docs/BRO_MONEY_HARNESS_Y0_RETRIAGE_20260421.json`
- Re-triage result: `present_actionable_count=0` for Y0->Y3 (yellow hygiene slice is `NO_MUTATION_VERIFIED` this cycle).

- ORANGE_CANDIDATE: **107**
- YELLOW: **105**
- TOTAL: **212**

## ORANGE_CANDIDATE Density By File
| Count | File |
|---:|---|
| 19 | `executor.py` |
| 15 | `scripts/canonical_paper_session.py` |
| 12 | `prodesk/preflight.py` |
| 8 | `prodesk/order_manager.py` |
| 8 | `scripts/run_integrity_audit.py` |
| 6 | `scripts/websocket_reliability_gate.py` |
| 5 | `prodesk/canonical_authority.py` |
| 4 | `prodesk/wallet/wallet_controller.py` |
| 3 | `scripts/outcome_truth_audit.py` |
| 2 | `prodesk/tx_manager.py` |
| 2 | `scripts/ci_gate.py` |
| 2 | `scripts/performance_budget_gate.py` |
| 2 | `scripts/readiness_gate.py` |
| 2 | `scripts/soak_hardening_gate.py` |
| 2 | `prodesk/alerts.py` |
| 2 | `prodesk/book_feed.py` |
| 2 | `prodesk/chainlink_feed.py` |
| 2 | `scripts/guardian_watchdog.py` |
| 2 | `scripts/nightly_soak_report.py` |
| 1 | `prodesk/market_data.py` |
| 1 | `scripts/prestart_gate.py` |
| 1 | `prodesk/artifact_identity.py` |
| 1 | `prodesk/run_contract.py` |
| 1 | `prodesk/state_store.py` |
| 1 | `scripts/ops_brief.py` |
| 1 | `scripts/ops_snapshot.py` |

## ORANGE_CANDIDATE (First 120)
| ID | File:Line | Tags | Snippet |
|---|---|---|---|
| T-001 | `executor.py:138` | `broad_exception` | `except Exception as exc:` |
| T-002 | `executor.py:826` | `broad_exception` | `except Exception as exc:` |
| T-003 | `executor.py:2175` | `broad_exception` | `except Exception as exc:` |
| T-004 | `executor.py:2270` | `broad_exception` | `except Exception as exc:` |
| T-005 | `executor.py:3559` | `broad_exception` | `except Exception as exc:` |
| T-006 | `executor.py:3570` | `broad_exception` | `except Exception as exc:` |
| T-007 | `executor.py:4795` | `broad_exception` | `except Exception as exc:` |
| T-008 | `executor.py:5113` | `broad_exception` | `except Exception:` |
| T-009 | `executor.py:5128` | `broad_exception` | `except Exception:` |
| T-010 | `executor.py:5187` | `broad_exception` | `except Exception:` |
| T-011 | `executor.py:5217` | `broad_exception` | `except Exception as exc:` |
| T-012 | `executor.py:5248` | `broad_exception` | `except Exception as exc:` |
| T-013 | `executor.py:5287` | `broad_exception` | `except Exception:` |
| T-014 | `executor.py:5339` | `broad_exception` | `except Exception as exc:` |
| T-015 | `executor.py:6443` | `broad_exception` | `except Exception as exc:` |
| T-016 | `executor.py:6659` | `broad_exception` | `except Exception as exc:` |
| T-017 | `executor.py:6675` | `broad_exception` | `except Exception as exc:` |
| T-018 | `executor.py:6947` | `broad_exception` | `except Exception as exc:` |
| T-019 | `executor.py:7010` | `broad_exception` | `except Exception as exc:` |
| T-020 | `prodesk/market_data.py:125` | `broad_exception` | `except Exception:` |
| T-021 | `prodesk/order_manager.py:349` | `broad_exception` | `except Exception as exc:` |
| T-022 | `prodesk/order_manager.py:1064` | `broad_exception` | `except Exception as exc:` |
| T-023 | `prodesk/order_manager.py:1125` | `suppressed_exception` | `with suppress(Exception):` |
| T-024 | `prodesk/order_manager.py:2079` | `suppressed_exception` | `with suppress(ValueError):` |
| T-025 | `prodesk/order_manager.py:2100` | `suppressed_exception` | `with suppress(ValueError):` |
| T-026 | `prodesk/order_manager.py:2117` | `suppressed_exception` | `with suppress(ValueError):` |
| T-027 | `prodesk/order_manager.py:2142` | `suppressed_exception` | `with suppress(ValueError):` |
| T-028 | `prodesk/order_manager.py:2184` | `suppressed_exception` | `with suppress(ValueError):` |
| T-029 | `prodesk/tx_manager.py:104` | `broad_exception` | `except Exception as exc:` |
| T-030 | `prodesk/tx_manager.py:120` | `broad_exception` | `except Exception as exc:` |
| T-031 | `prodesk/wallet/wallet_controller.py:229` | `broad_exception` | `except Exception:` |
| T-032 | `prodesk/wallet/wallet_controller.py:803` | `broad_exception` | `except Exception as exc:` |
| T-033 | `prodesk/wallet/wallet_controller.py:822` | `broad_exception` | `except Exception as exc:` |
| T-034 | `prodesk/wallet/wallet_controller.py:1051` | `broad_exception` | `except Exception as exc:` |
| T-035 | `scripts/canonical_paper_session.py:133` | `broad_exception` | `except Exception:` |
| T-036 | `scripts/canonical_paper_session.py:164` | `broad_exception` | `except Exception as exc:` |
| T-037 | `scripts/canonical_paper_session.py:206` | `broad_exception` | `except Exception as exc:` |
| T-038 | `scripts/canonical_paper_session.py:248` | `broad_exception` | `except Exception:` |
| T-039 | `scripts/canonical_paper_session.py:290` | `subprocess_without_timeout` | `proc = subprocess.run(` |
| T-040 | `scripts/canonical_paper_session.py:324` | `broad_exception` | `except Exception:` |
| T-041 | `scripts/canonical_paper_session.py:328` | `broad_exception` | `except Exception:` |
| T-042 | `scripts/canonical_paper_session.py:360` | `broad_exception` | `except Exception:` |
| T-043 | `scripts/canonical_paper_session.py:611` | `subprocess_without_timeout` | `return subprocess.run(` |
| T-044 | `scripts/canonical_paper_session.py:816` | `broad_exception` | `except Exception as exc:` |
| T-045 | `scripts/canonical_paper_session.py:825` | `broad_exception` | `except Exception:` |
| T-046 | `scripts/canonical_paper_session.py:901` | `broad_exception` | `except Exception:` |
| T-047 | `scripts/canonical_paper_session.py:1050` | `subprocess_without_timeout` | `proc = subprocess.run(` |
| T-048 | `scripts/canonical_paper_session.py:1230` | `subprocess_without_timeout` | `proc = subprocess.run(` |
| T-049 | `scripts/canonical_paper_session.py:1380` | `broad_exception` | `except Exception as exc:` |
| T-050 | `scripts/ci_gate.py:19` | `subprocess_without_timeout` | `result = subprocess.run(cmd, check=False)` |
| T-051 | `scripts/ci_gate.py:56` | `subprocess_without_timeout` | `editable = subprocess.run([py, "-m", "pip", "install", "-e", "."], check=False)` |
| T-052 | `scripts/performance_budget_gate.py:19` | `broad_exception` | `except Exception:` |
| T-053 | `scripts/performance_budget_gate.py:44` | `broad_exception` | `except Exception:` |
| T-054 | `scripts/prestart_gate.py:23` | `broad_exception` | `except Exception:` |
| T-055 | `scripts/readiness_gate.py:98` | `broad_exception` | `except Exception as exc:` |
| T-056 | `scripts/readiness_gate.py:130` | `broad_exception` | `except Exception:` |
| T-057 | `scripts/run_integrity_audit.py:47` | `broad_exception` | `except Exception:` |
| T-058 | `scripts/run_integrity_audit.py:59` | `broad_exception` | `except Exception:` |
| T-059 | `scripts/run_integrity_audit.py:67` | `broad_exception` | `except Exception:` |
| T-060 | `scripts/run_integrity_audit.py:83` | `broad_exception` | `except Exception:` |
| T-061 | `scripts/run_integrity_audit.py:87` | `broad_exception` | `except Exception:` |
| T-062 | `scripts/run_integrity_audit.py:138` | `broad_exception` | `except Exception:` |
| T-063 | `scripts/run_integrity_audit.py:147` | `broad_exception` | `except Exception:` |
| T-064 | `scripts/run_integrity_audit.py:211` | `broad_exception` | `except Exception as exc:` |
| T-065 | `scripts/soak_hardening_gate.py:33` | `broad_exception` | `except Exception:` |
| T-066 | `scripts/soak_hardening_gate.py:43` | `broad_exception` | `except Exception:` |
| T-067 | `scripts/websocket_reliability_gate.py:31` | `broad_exception` | `except Exception:` |
| T-068 | `scripts/websocket_reliability_gate.py:41` | `broad_exception` | `except Exception:` |
| T-069 | `scripts/websocket_reliability_gate.py:134` | `broad_exception` | `except Exception:` |
| T-070 | `scripts/websocket_reliability_gate.py:153` | `broad_exception` | `except Exception:` |
| T-071 | `scripts/websocket_reliability_gate.py:157` | `broad_exception` | `except Exception:` |
| T-072 | `scripts/websocket_reliability_gate.py:533` | `broad_exception` | `except Exception:` |
| T-073 | `prodesk/alerts.py:56` | `broad_exception` | `except Exception as exc:` |
| T-074 | `prodesk/alerts.py:85` | `broad_exception` | `except Exception as exc:` |
| T-075 | `prodesk/artifact_identity.py:17` | `broad_exception` | `except Exception:` |
| T-076 | `prodesk/book_feed.py:199` | `broad_exception` | `except Exception as exc:` |
| T-077 | `prodesk/book_feed.py:266` | `broad_exception` | `except Exception as exc:` |
| T-078 | `prodesk/canonical_authority.py:135` | `broad_exception` | `except Exception:` |
| T-079 | `prodesk/canonical_authority.py:154` | `broad_exception` | `except Exception as exc:` |
| T-080 | `prodesk/canonical_authority.py:399` | `broad_exception` | `except Exception:` |
| T-081 | `prodesk/canonical_authority.py:441` | `broad_exception` | `except Exception as exc:` |
| T-082 | `prodesk/canonical_authority.py:456` | `broad_exception` | `except Exception as exc:` |
| T-083 | `prodesk/chainlink_feed.py:177` | `broad_exception` | `except Exception as exc:` |
| T-084 | `prodesk/chainlink_feed.py:223` | `broad_exception` | `except Exception as exc:` |
| T-085 | `prodesk/preflight.py:35` | `broad_exception` | `except Exception as exc:` |
| T-086 | `prodesk/preflight.py:45` | `broad_exception` | `except Exception as exc:` |
| T-087 | `prodesk/preflight.py:89` | `broad_exception` | `except Exception as exc:` |
| T-088 | `prodesk/preflight.py:94` | `broad_exception` | `except Exception as exc:` |
| T-089 | `prodesk/preflight.py:109` | `broad_exception` | `except Exception as exc:` |
| T-090 | `prodesk/preflight.py:120` | `broad_exception` | `except Exception:` |
| T-091 | `prodesk/preflight.py:126` | `broad_exception` | `except Exception:` |
| T-092 | `prodesk/preflight.py:132` | `broad_exception` | `except Exception:` |
| T-093 | `prodesk/preflight.py:151` | `broad_exception` | `except Exception as exc:` |
| T-094 | `prodesk/preflight.py:207` | `broad_exception` | `except Exception:` |
| T-095 | `prodesk/preflight.py:248` | `broad_exception` | `except Exception as exc:` |
| T-096 | `prodesk/preflight.py:272` | `broad_exception` | `except Exception:` |
| T-097 | `prodesk/run_contract.py:115` | `broad_exception` | `except Exception as exc:` |
| T-098 | `prodesk/state_store.py:46` | `broad_exception` | `except Exception:` |
| T-099 | `scripts/guardian_watchdog.py:66` | `broad_exception` | `except Exception:` |
| T-100 | `scripts/guardian_watchdog.py:95` | `broad_exception` | `except Exception:` |
| T-101 | `scripts/nightly_soak_report.py:54` | `broad_exception` | `except Exception:` |
| T-102 | `scripts/nightly_soak_report.py:121` | `broad_exception` | `except Exception:` |
| T-103 | `scripts/ops_brief.py:19` | `broad_exception` | `except Exception:` |
| T-104 | `scripts/ops_snapshot.py:21` | `broad_exception` | `except Exception as exc:` |
| T-105 | `scripts/outcome_truth_audit.py:218` | `broad_exception` | `except Exception:` |
| T-106 | `scripts/outcome_truth_audit.py:300` | `broad_exception` | `except Exception as exc:` |
| T-107 | `scripts/outcome_truth_audit.py:764` | `broad_exception` | `except Exception as exc:` |

## YELLOW (First 120)
| ID | File:Line | Tags | Snippet |
|---|---|---|---|
| T-108 | `executor.py:2682` | `return_true` | `return True` |
| T-109 | `executor.py:2851` | `pass_path` | `verdict = "pass" if stage not in {STAGE_UNKNOWN, STAGE_EXPIRED} else "fail"` |
| T-110 | `executor.py:2973` | `return_true` | `return True, tick_age_sec, ""` |
| T-111 | `executor.py:3494` | `return_true` | `return True` |
| T-112 | `executor.py:4273` | `return_true` | `return True` |
| T-113 | `executor.py:4491` | `return_true` | `return True` |
| T-114 | `executor.py:4683` | `return_true` | `return True` |
| T-115 | `executor.py:5216` | `return_true` | `return True, reason[:240]` |
| T-116 | `executor.py:5288` | `pass_path` | `pass` |
| T-117 | `executor.py:5940` | `pass_path` | `if str(info.get("doctrine_gate_verdict", "fail")) != "pass":` |
| T-118 | `prodesk/alerts.py:30` | `network_without_timeout_literal` | `self.session = requests.Session()` |
| T-119 | `prodesk/alerts.py:64` | `return_true` | `return True` |
| T-120 | `prodesk/alerts.py:84` | `return_true` | `return True` |
| T-121 | `prodesk/book_feed.py:63` | `pass_path` | `pass` |
| T-122 | `prodesk/canonical_authority.py:134` | `return_true` | `return True` |
| T-123 | `prodesk/chainlink_feed.py:32` | `pass_path` | `pass` |
| T-124 | `prodesk/chainlink_feed.py:370` | `return_true` | `return True, "same_source_ts_revision"` |
| T-125 | `prodesk/chainlink_feed.py:371` | `return_true` | `return True, "newer_source_ts"` |
| T-126 | `prodesk/chainlink_feed.py:376` | `return_true` | `return True, "timestamp_upgrade"` |
| T-127 | `prodesk/chainlink_feed.py:383` | `return_true` | `return True, "same_receive_monotonic_revision"` |
| T-128 | `prodesk/chainlink_feed.py:384` | `return_true` | `return True, "newer_receive_monotonic"` |
| T-129 | `prodesk/chainlink_feed.py:431` | `return_true` | `return True` |
| T-130 | `prodesk/chainlink_feed.py:437` | `return_true` | `return True` |
| T-131 | `prodesk/gateway.py:15` | `pass_path` | `pass` |
| T-132 | `prodesk/gateway.py:19` | `pass_path` | `pass` |
| T-133 | `prodesk/gateway.py:140` | `return_true` | `return True` |
| T-134 | `prodesk/gateway.py:381` | `return_true` | `return True` |
| T-135 | `prodesk/gateway.py:763` | `return_true` | `return True` |
| T-136 | `prodesk/latency_verifier.py:192` | `return_true` | `return True` |
| T-137 | `prodesk/market_data.py:56` | `network_without_timeout_literal` | `session: requests.Session,` |
| T-138 | `prodesk/market_data.py:66` | `network_without_timeout_literal` | `except requests.RequestException:` |
| T-139 | `prodesk/market_data.py:105` | `network_without_timeout_literal` | `self._sessions: List[requests.Session] = []` |
| T-140 | `prodesk/market_data.py:108` | `network_without_timeout_literal` | `def _session(self) -> requests.Session:` |
| T-141 | `prodesk/market_data.py:111` | `network_without_timeout_literal` | `session = requests.Session()` |
| T-142 | `prodesk/market_discovery.py:34` | `pass_path` | `pass` |
| T-143 | `prodesk/market_discovery.py:125` | `return_true` | `return True` |
| T-144 | `prodesk/market_discovery.py:132` | `network_without_timeout_literal` | `session: requests.Session,` |
| T-145 | `prodesk/market_discovery.py:142` | `network_without_timeout_literal` | `except requests.RequestException:` |
| T-146 | `prodesk/market_discovery.py:214` | `network_without_timeout_literal` | `self.session = requests.Session()` |
| T-147 | `prodesk/market_discovery.py:230` | `return_true` | `return True` |
| T-148 | `prodesk/market_discovery.py:234` | `return_true` | `return True` |
| T-149 | `prodesk/market_discovery.py:284` | `return_true` | `return True` |
| T-150 | `prodesk/market_discovery.py:287` | `return_true` | `return True` |
| T-151 | `prodesk/market_discovery.py:304` | `return_true` | `return True` |
| T-152 | `prodesk/order_manager.py:317` | `return_true` | `return True` |
| T-153 | `prodesk/order_manager.py:319` | `return_true` | `return True` |
| T-154 | `prodesk/order_manager.py:322` | `return_true` | `return True` |
| T-155 | `prodesk/order_manager.py:413` | `return_true` | `return True` |
| T-156 | `prodesk/order_manager.py:415` | `return_true` | `return True` |
| T-157 | `prodesk/order_manager.py:1246` | `return_true` | `return True` |
| T-158 | `prodesk/order_manager.py:1452` | `return_true` | `return True` |
| T-159 | `prodesk/order_manager.py:1542` | `return_true` | `return True` |
| T-160 | `prodesk/preflight.py:241` | `network_without_timeout_literal` | `session = requests.Session()` |
| T-161 | `prodesk/preflight.py:258` | `network_without_timeout_literal` | `session = requests.Session()` |
| T-162 | `prodesk/risk.py:150` | `return_true` | `return True` |
| T-163 | `prodesk/risk.py:383` | `return_true` | `return True` |
| T-164 | `prodesk/run_contract.py:264` | `return_true` | `return True` |
| T-165 | `prodesk/runtime_semantics.py:92` | `return_true` | `return True` |
| T-166 | `prodesk/runtime_semantics.py:128` | `return_true` | `return True` |
| T-167 | `prodesk/runtime_semantics.py:145` | `return_true` | `return True` |
| T-168 | `prodesk/security.py:127` | `return_true` | `return True` |
| T-169 | `prodesk/security.py:129` | `return_true` | `return True` |
| T-170 | `prodesk/security.py:131` | `return_true` | `return True` |
| T-171 | `prodesk/security.py:185` | `return_true` | `return True` |
| T-172 | `prodesk/security.py:217` | `return_true` | `return True` |
| T-173 | `prodesk/state_store.py:47` | `pass_path` | `pass` |
| T-174 | `prodesk/wallet/wallet_controller.py:715` | `return_true` | `return True` |
| T-175 | `prodesk/wallet/wallet_reservations.py:68` | `return_true` | `return True, "wallet_lock_id_idempotent_completed"` |
| T-176 | `prodesk/wallet/wallet_reservations.py:72` | `return_true` | `return True, "wallet_lock_id_idempotent_order_exists"` |
| T-177 | `prodesk/wallet/wallet_reservations.py:82` | `return_true` | `return True, "ok"` |
| T-178 | `scripts/canonical_paper_session.py:304` | `return_true` | `return True` |
| T-179 | `scripts/canonical_paper_session.py:311` | `return_true` | `return True` |
| T-180 | `scripts/canonical_paper_session.py:458` | `pass_path` | `status = "pass"` |
| T-181 | `scripts/ci_gate.py:468` | `pass_path` | `raise SystemExit("readiness_gate fixture did not pass any stage")` |
| T-182 | `scripts/guardian_watchdog.py:241` | `return_true` | `return True, "status_missing", details` |
| T-183 | `scripts/guardian_watchdog.py:247` | `return_true` | `return True, "status_ts_invalid", details` |
| T-184 | `scripts/guardian_watchdog.py:263` | `return_true` | `return True, "status_stale", details` |
| T-185 | `scripts/guardian_watchdog.py:274` | `return_true` | `return True, "kill_switch_engaged", details` |
| T-186 | `scripts/guardian_watchdog.py:279` | `return_true` | `return True, "operating_mode_degraded", details` |
| T-187 | `scripts/guardian_watchdog.py:282` | `return_true` | `return True, "error_burst", details` |
| T-188 | `scripts/guardian_watchdog.py:310` | `return_true` | `return True, "chainlink_disconnected", details` |
| T-189 | `scripts/guardian_watchdog.py:313` | `return_true` | `return True, "chainlink_disconnected", details` |
| T-190 | `scripts/guardian_watchdog.py:331` | `return_true` | `return True, "book_feed_disconnected", details` |
| T-191 | `scripts/guardian_watchdog.py:334` | `return_true` | `return True, "book_feed_disconnected", details` |
| T-192 | `scripts/nightly_soak_report.py:136` | `return_true` | `return True` |
| T-193 | `scripts/nightly_soak_report.py:145` | `return_true` | `return True` |
| T-194 | `scripts/nightly_soak_report.py:148` | `return_true` | `return True` |
| T-195 | `scripts/nightly_soak_report.py:153` | `return_true` | `return True` |
| T-196 | `scripts/nightly_soak_report.py:156` | `return_true` | `return True` |
| T-197 | `scripts/nightly_soak_report.py:159` | `return_true` | `return True` |
| T-198 | `scripts/nightly_soak_report.py:162` | `return_true` | `return True` |
| T-199 | `scripts/nightly_soak_report.py:165` | `return_true` | `return True` |
| T-200 | `scripts/nightly_soak_report.py:168` | `return_true` | `return True` |
| T-201 | `scripts/nightly_soak_report.py:175` | `return_true` | `return True` |
| T-202 | `scripts/nightly_soak_report.py:180` | `return_true` | `return True` |
| T-203 | `scripts/nightly_soak_report.py:183` | `return_true` | `return True` |
| T-204 | `scripts/nightly_soak_report.py:200` | `return_true` | `return True` |
| T-205 | `scripts/nightly_soak_report.py:203` | `return_true` | `return True` |
| T-206 | `scripts/nightly_soak_report.py:1842` | `return_true` | `return True` |
| T-207 | `scripts/outcome_truth_audit.py:1516` | `pass_path` | `horizon_consistency_check = "pass" if len(horizon_findings) == 0 else "fail"` |
| T-208 | `scripts/performance_budget_gate.py:236` | `pass_path` | `note="must pass with breach ratio check",` |
| T-209 | `scripts/performance_budget_gate.py:248` | `pass_path` | `note="must pass with breach rows check",` |
| T-210 | `scripts/performance_budget_gate.py:284` | `pass_path` | `note="must pass with breach ratio check",` |
| T-211 | `scripts/performance_budget_gate.py:296` | `pass_path` | `note="must pass with breach rows check",` |
| T-212 | `scripts/websocket_reliability_gate.py:75` | `return_true` | `return True` |
