# BRO Money Harness Casualty Board (2026-04-20)

Total candidates: **212**  
ORANGE_CANDIDATE: **72**  
YELLOW: **140**

## Density By File
| Count | File |
|---:|---|
| 29 | `executor.py` |
| 18 | `scripts/canonical_paper_session.py` |
| 17 | `scripts/nightly_soak_report.py` |
| 16 | `prodesk/order_manager.py` |
| 14 | `prodesk/preflight.py` |
| 12 | `scripts/guardian_watchdog.py` |
| 10 | `prodesk/chainlink_feed.py` |
| 10 | `prodesk/market_discovery.py` |
| 8 | `scripts/run_integrity_audit.py` |
| 7 | `scripts/websocket_reliability_gate.py` |
| 6 | `prodesk/market_data.py` |
| 6 | `scripts/performance_budget_gate.py` |
| 6 | `prodesk/canonical_authority.py` |
| 5 | `prodesk/wallet/wallet_controller.py` |
| 5 | `prodesk/alerts.py` |
| 5 | `prodesk/gateway.py` |
| 5 | `prodesk/security.py` |
| 4 | `scripts/outcome_truth_audit.py` |
| 3 | `scripts/ci_gate.py` |
| 3 | `prodesk/book_feed.py` |
| 3 | `prodesk/runtime_semantics.py` |
| 3 | `prodesk/wallet/wallet_reservations.py` |
| 2 | `prodesk/tx_manager.py` |
| 2 | `scripts/readiness_gate.py` |
| 2 | `scripts/soak_hardening_gate.py` |
| 2 | `prodesk/risk.py` |
| 2 | `prodesk/run_contract.py` |
| 2 | `prodesk/state_store.py` |
| 1 | `scripts/prestart_gate.py` |
| 1 | `prodesk/artifact_identity.py` |

## Density By Tag
| Count | Tag |
|---:|---|
| 95 | `broad_exception` |
| 78 | `return_true` |
| 16 | `pass_path` |
| 11 | `network_without_timeout_literal` |
| 6 | `suppressed_exception` |
| 6 | `subprocess_without_timeout` |

## ORANGE_CANDIDATE List
| ID | File:Line | Tags | Snippet |
|---|---|---|---|
| C-001 | `executor.py:138` | `broad_exception` | `except Exception as exc:` |
| C-002 | `executor.py:826` | `broad_exception` | `except Exception as exc:` |
| C-003 | `executor.py:2175` | `broad_exception` | `except Exception as exc:` |
| C-004 | `executor.py:2270` | `broad_exception` | `except Exception as exc:` |
| C-005 | `executor.py:3559` | `broad_exception` | `except Exception as exc:` |
| C-006 | `executor.py:3570` | `broad_exception` | `except Exception as exc:` |
| C-007 | `executor.py:4795` | `broad_exception` | `except Exception as exc:` |
| C-008 | `executor.py:5113` | `broad_exception` | `except Exception:` |
| C-009 | `executor.py:5128` | `broad_exception` | `except Exception:` |
| C-010 | `executor.py:5187` | `broad_exception` | `except Exception:` |
| C-011 | `executor.py:5217` | `broad_exception` | `except Exception as exc:` |
| C-012 | `executor.py:5248` | `broad_exception` | `except Exception as exc:` |
| C-013 | `executor.py:5287` | `broad_exception` | `except Exception:` |
| C-014 | `executor.py:5339` | `broad_exception` | `except Exception as exc:` |
| C-015 | `executor.py:6443` | `broad_exception` | `except Exception as exc:` |
| C-016 | `executor.py:6659` | `broad_exception` | `except Exception as exc:` |
| C-017 | `executor.py:6675` | `broad_exception` | `except Exception as exc:` |
| C-018 | `executor.py:6947` | `broad_exception` | `except Exception as exc:` |
| C-019 | `executor.py:7010` | `broad_exception` | `except Exception as exc:` |
| C-020 | `prodesk/market_data.py:125` | `broad_exception` | `except Exception:` |
| C-021 | `prodesk/order_manager.py:349` | `broad_exception` | `except Exception as exc:` |
| C-022 | `prodesk/order_manager.py:1064` | `broad_exception` | `except Exception as exc:` |
| C-023 | `prodesk/order_manager.py:1125` | `suppressed_exception` | `with suppress(Exception):` |
| C-024 | `prodesk/order_manager.py:2079` | `suppressed_exception` | `with suppress(ValueError):` |
| C-025 | `prodesk/order_manager.py:2100` | `suppressed_exception` | `with suppress(ValueError):` |
| C-026 | `prodesk/order_manager.py:2117` | `suppressed_exception` | `with suppress(ValueError):` |
| C-027 | `prodesk/order_manager.py:2142` | `suppressed_exception` | `with suppress(ValueError):` |
| C-028 | `prodesk/order_manager.py:2184` | `suppressed_exception` | `with suppress(ValueError):` |
| C-029 | `prodesk/tx_manager.py:104` | `broad_exception` | `except Exception as exc:` |
| C-030 | `prodesk/tx_manager.py:120` | `broad_exception` | `except Exception as exc:` |
| C-031 | `prodesk/wallet/wallet_controller.py:229` | `broad_exception` | `except Exception:` |
| C-032 | `prodesk/wallet/wallet_controller.py:803` | `broad_exception` | `except Exception as exc:` |
| C-033 | `prodesk/wallet/wallet_controller.py:822` | `broad_exception` | `except Exception as exc:` |
| C-034 | `prodesk/wallet/wallet_controller.py:1051` | `broad_exception` | `except Exception as exc:` |
| C-035 | `scripts/canonical_paper_session.py:133` | `broad_exception` | `except Exception:` |
| C-036 | `scripts/canonical_paper_session.py:164` | `broad_exception` | `except Exception as exc:` |
| C-037 | `scripts/canonical_paper_session.py:206` | `broad_exception` | `except Exception as exc:` |
| C-038 | `scripts/canonical_paper_session.py:248` | `broad_exception` | `except Exception:` |
| C-039 | `scripts/canonical_paper_session.py:290` | `subprocess_without_timeout` | `proc = subprocess.run(` |
| C-040 | `scripts/canonical_paper_session.py:324` | `broad_exception` | `except Exception:` |
| C-041 | `scripts/canonical_paper_session.py:328` | `broad_exception` | `except Exception:` |
| C-042 | `scripts/canonical_paper_session.py:360` | `broad_exception` | `except Exception:` |
| C-043 | `scripts/canonical_paper_session.py:611` | `subprocess_without_timeout` | `return subprocess.run(` |
| C-044 | `scripts/canonical_paper_session.py:816` | `broad_exception` | `except Exception as exc:` |
| C-045 | `scripts/canonical_paper_session.py:825` | `broad_exception` | `except Exception:` |
| C-046 | `scripts/canonical_paper_session.py:901` | `broad_exception` | `except Exception:` |
| C-047 | `scripts/canonical_paper_session.py:1050` | `subprocess_without_timeout` | `proc = subprocess.run(` |
| C-048 | `scripts/canonical_paper_session.py:1230` | `subprocess_without_timeout` | `proc = subprocess.run(` |
| C-049 | `scripts/canonical_paper_session.py:1380` | `broad_exception` | `except Exception as exc:` |
| C-050 | `scripts/ci_gate.py:19` | `subprocess_without_timeout` | `result = subprocess.run(cmd, check=False)` |
| C-051 | `scripts/ci_gate.py:56` | `subprocess_without_timeout` | `editable = subprocess.run([py, "-m", "pip", "install", "-e", "."], check=False)` |
| C-052 | `scripts/performance_budget_gate.py:19` | `broad_exception` | `except Exception:` |
| C-053 | `scripts/performance_budget_gate.py:44` | `broad_exception` | `except Exception:` |
| C-054 | `scripts/prestart_gate.py:23` | `broad_exception` | `except Exception:` |
| C-055 | `scripts/readiness_gate.py:98` | `broad_exception` | `except Exception as exc:` |
| C-056 | `scripts/readiness_gate.py:130` | `broad_exception` | `except Exception:` |
| C-057 | `scripts/run_integrity_audit.py:47` | `broad_exception` | `except Exception:` |
| C-058 | `scripts/run_integrity_audit.py:59` | `broad_exception` | `except Exception:` |
| C-059 | `scripts/run_integrity_audit.py:67` | `broad_exception` | `except Exception:` |
| C-060 | `scripts/run_integrity_audit.py:83` | `broad_exception` | `except Exception:` |
| C-061 | `scripts/run_integrity_audit.py:87` | `broad_exception` | `except Exception:` |
| C-062 | `scripts/run_integrity_audit.py:138` | `broad_exception` | `except Exception:` |
| C-063 | `scripts/run_integrity_audit.py:147` | `broad_exception` | `except Exception:` |
| C-064 | `scripts/run_integrity_audit.py:211` | `broad_exception` | `except Exception as exc:` |
| C-065 | `scripts/soak_hardening_gate.py:33` | `broad_exception` | `except Exception:` |
| C-066 | `scripts/soak_hardening_gate.py:43` | `broad_exception` | `except Exception:` |
| C-067 | `scripts/websocket_reliability_gate.py:31` | `broad_exception` | `except Exception:` |
| C-068 | `scripts/websocket_reliability_gate.py:41` | `broad_exception` | `except Exception:` |
| C-069 | `scripts/websocket_reliability_gate.py:134` | `broad_exception` | `except Exception:` |
| C-070 | `scripts/websocket_reliability_gate.py:153` | `broad_exception` | `except Exception:` |
| C-071 | `scripts/websocket_reliability_gate.py:157` | `broad_exception` | `except Exception:` |
| C-072 | `scripts/websocket_reliability_gate.py:533` | `broad_exception` | `except Exception:` |

## YELLOW List (Top 100 by deterministic order)
| ID | File:Line | Tags | Snippet |
|---|---|---|---|
| C-073 | `executor.py:2682` | `return_true` | `return True` |
| C-074 | `executor.py:2851` | `pass_path` | `verdict = "pass" if stage not in {STAGE_UNKNOWN, STAGE_EXPIRED} else "fail"` |
| C-075 | `executor.py:2973` | `return_true` | `return True, tick_age_sec, ""` |
| C-076 | `executor.py:3494` | `return_true` | `return True` |
| C-077 | `executor.py:4273` | `return_true` | `return True` |
| C-078 | `executor.py:4491` | `return_true` | `return True` |
| C-079 | `executor.py:4683` | `return_true` | `return True` |
| C-080 | `executor.py:5216` | `return_true` | `return True, reason[:240]` |
| C-081 | `executor.py:5288` | `pass_path` | `pass` |
| C-082 | `executor.py:5940` | `pass_path` | `if str(info.get("doctrine_gate_verdict", "fail")) != "pass":` |
| C-083 | `prodesk/alerts.py:30` | `network_without_timeout_literal` | `self.session = requests.Session()` |
| C-084 | `prodesk/alerts.py:56` | `broad_exception` | `except Exception as exc:` |
| C-085 | `prodesk/alerts.py:64` | `return_true` | `return True` |
| C-086 | `prodesk/alerts.py:84` | `return_true` | `return True` |
| C-087 | `prodesk/alerts.py:85` | `broad_exception` | `except Exception as exc:` |
| C-088 | `prodesk/artifact_identity.py:17` | `broad_exception` | `except Exception:` |
| C-089 | `prodesk/book_feed.py:63` | `pass_path` | `pass` |
| C-090 | `prodesk/book_feed.py:199` | `broad_exception` | `except Exception as exc:` |
| C-091 | `prodesk/book_feed.py:266` | `broad_exception` | `except Exception as exc:` |
| C-092 | `prodesk/canonical_authority.py:134` | `return_true` | `return True` |
| C-093 | `prodesk/canonical_authority.py:135` | `broad_exception` | `except Exception:` |
| C-094 | `prodesk/canonical_authority.py:154` | `broad_exception` | `except Exception as exc:` |
| C-095 | `prodesk/canonical_authority.py:399` | `broad_exception` | `except Exception:` |
| C-096 | `prodesk/canonical_authority.py:441` | `broad_exception` | `except Exception as exc:` |
| C-097 | `prodesk/canonical_authority.py:456` | `broad_exception` | `except Exception as exc:` |
| C-098 | `prodesk/chainlink_feed.py:32` | `pass_path` | `pass` |
| C-099 | `prodesk/chainlink_feed.py:177` | `broad_exception` | `except Exception as exc:` |
| C-100 | `prodesk/chainlink_feed.py:223` | `broad_exception` | `except Exception as exc:` |
| C-101 | `prodesk/chainlink_feed.py:370` | `return_true` | `return True, "same_source_ts_revision"` |
| C-102 | `prodesk/chainlink_feed.py:371` | `return_true` | `return True, "newer_source_ts"` |
| C-103 | `prodesk/chainlink_feed.py:376` | `return_true` | `return True, "timestamp_upgrade"` |
| C-104 | `prodesk/chainlink_feed.py:383` | `return_true` | `return True, "same_receive_monotonic_revision"` |
| C-105 | `prodesk/chainlink_feed.py:384` | `return_true` | `return True, "newer_receive_monotonic"` |
| C-106 | `prodesk/chainlink_feed.py:431` | `return_true` | `return True` |
| C-107 | `prodesk/chainlink_feed.py:437` | `return_true` | `return True` |
| C-108 | `prodesk/gateway.py:15` | `pass_path` | `pass` |
| C-109 | `prodesk/gateway.py:19` | `pass_path` | `pass` |
| C-110 | `prodesk/gateway.py:140` | `return_true` | `return True` |
| C-111 | `prodesk/gateway.py:381` | `return_true` | `return True` |
| C-112 | `prodesk/gateway.py:763` | `return_true` | `return True` |
| C-113 | `prodesk/latency_verifier.py:192` | `return_true` | `return True` |
| C-114 | `prodesk/market_data.py:56` | `network_without_timeout_literal` | `session: requests.Session,` |
| C-115 | `prodesk/market_data.py:66` | `network_without_timeout_literal` | `except requests.RequestException:` |
| C-116 | `prodesk/market_data.py:105` | `network_without_timeout_literal` | `self._sessions: List[requests.Session] = []` |
| C-117 | `prodesk/market_data.py:108` | `network_without_timeout_literal` | `def _session(self) -> requests.Session:` |
| C-118 | `prodesk/market_data.py:111` | `network_without_timeout_literal` | `session = requests.Session()` |
| C-119 | `prodesk/market_discovery.py:34` | `pass_path` | `pass` |
| C-120 | `prodesk/market_discovery.py:125` | `return_true` | `return True` |
| C-121 | `prodesk/market_discovery.py:132` | `network_without_timeout_literal` | `session: requests.Session,` |
| C-122 | `prodesk/market_discovery.py:142` | `network_without_timeout_literal` | `except requests.RequestException:` |
| C-123 | `prodesk/market_discovery.py:214` | `network_without_timeout_literal` | `self.session = requests.Session()` |
| C-124 | `prodesk/market_discovery.py:230` | `return_true` | `return True` |
| C-125 | `prodesk/market_discovery.py:234` | `return_true` | `return True` |
| C-126 | `prodesk/market_discovery.py:284` | `return_true` | `return True` |
| C-127 | `prodesk/market_discovery.py:287` | `return_true` | `return True` |
| C-128 | `prodesk/market_discovery.py:304` | `return_true` | `return True` |
| C-129 | `prodesk/order_manager.py:317` | `return_true` | `return True` |
| C-130 | `prodesk/order_manager.py:319` | `return_true` | `return True` |
| C-131 | `prodesk/order_manager.py:322` | `return_true` | `return True` |
| C-132 | `prodesk/order_manager.py:413` | `return_true` | `return True` |
| C-133 | `prodesk/order_manager.py:415` | `return_true` | `return True` |
| C-134 | `prodesk/order_manager.py:1246` | `return_true` | `return True` |
| C-135 | `prodesk/order_manager.py:1452` | `return_true` | `return True` |
| C-136 | `prodesk/order_manager.py:1542` | `return_true` | `return True` |
| C-137 | `prodesk/preflight.py:35` | `broad_exception` | `except Exception as exc:` |
| C-138 | `prodesk/preflight.py:45` | `broad_exception` | `except Exception as exc:` |
| C-139 | `prodesk/preflight.py:89` | `broad_exception` | `except Exception as exc:` |
| C-140 | `prodesk/preflight.py:94` | `broad_exception` | `except Exception as exc:` |
| C-141 | `prodesk/preflight.py:109` | `broad_exception` | `except Exception as exc:` |
| C-142 | `prodesk/preflight.py:120` | `broad_exception` | `except Exception:` |
| C-143 | `prodesk/preflight.py:126` | `broad_exception` | `except Exception:` |
| C-144 | `prodesk/preflight.py:132` | `broad_exception` | `except Exception:` |
| C-145 | `prodesk/preflight.py:151` | `broad_exception` | `except Exception as exc:` |
| C-146 | `prodesk/preflight.py:207` | `broad_exception` | `except Exception:` |
| C-147 | `prodesk/preflight.py:241` | `network_without_timeout_literal` | `session = requests.Session()` |
| C-148 | `prodesk/preflight.py:248` | `broad_exception` | `except Exception as exc:` |
| C-149 | `prodesk/preflight.py:258` | `network_without_timeout_literal` | `session = requests.Session()` |
| C-150 | `prodesk/preflight.py:272` | `broad_exception` | `except Exception:` |
| C-151 | `prodesk/risk.py:150` | `return_true` | `return True` |
| C-152 | `prodesk/risk.py:383` | `return_true` | `return True` |
| C-153 | `prodesk/run_contract.py:115` | `broad_exception` | `except Exception as exc:` |
| C-154 | `prodesk/run_contract.py:264` | `return_true` | `return True` |
| C-155 | `prodesk/runtime_semantics.py:92` | `return_true` | `return True` |
| C-156 | `prodesk/runtime_semantics.py:128` | `return_true` | `return True` |
| C-157 | `prodesk/runtime_semantics.py:145` | `return_true` | `return True` |
| C-158 | `prodesk/security.py:127` | `return_true` | `return True` |
| C-159 | `prodesk/security.py:129` | `return_true` | `return True` |
| C-160 | `prodesk/security.py:131` | `return_true` | `return True` |
| C-161 | `prodesk/security.py:185` | `return_true` | `return True` |
| C-162 | `prodesk/security.py:217` | `return_true` | `return True` |
| C-163 | `prodesk/state_store.py:46` | `broad_exception` | `except Exception:` |
| C-164 | `prodesk/state_store.py:47` | `pass_path` | `pass` |
| C-165 | `prodesk/wallet/wallet_controller.py:715` | `return_true` | `return True` |
| C-166 | `prodesk/wallet/wallet_reservations.py:68` | `return_true` | `return True, "wallet_lock_id_idempotent_completed"` |
| C-167 | `prodesk/wallet/wallet_reservations.py:72` | `return_true` | `return True, "wallet_lock_id_idempotent_order_exists"` |
| C-168 | `prodesk/wallet/wallet_reservations.py:82` | `return_true` | `return True, "ok"` |
| C-169 | `scripts/canonical_paper_session.py:304` | `return_true` | `return True` |
| C-170 | `scripts/canonical_paper_session.py:311` | `return_true` | `return True` |
| C-171 | `scripts/canonical_paper_session.py:458` | `pass_path` | `status = "pass"` |
| C-172 | `scripts/ci_gate.py:468` | `pass_path` | `raise SystemExit("readiness_gate fixture did not pass any stage")` |
