import unittest

from prodesk.runtime_semantics import (
    RUNTIME_CLASS_INVALID_DEADLOCK,
    RUNTIME_CLASS_INVALID_SAFETY,
    RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION,
    RUNTIME_CLASS_VALID_ACTIVE,
    RUNTIME_CLASS_VALID_SCAN,
    classify_runtime,
    resolve_guard_connectivity_requirements,
)


class RuntimeSemanticsTests(unittest.TestCase):
    def test_guard_requirements_disable_market_truth_for_scan_phase(self):
        status_row = {
            "lifecycle_phase": "scan",
            "active_targets_present": False,
            "market_truth_required": False,
        }
        out = resolve_guard_connectivity_requirements(
            status_row=status_row,
            require_book_feed_connected_config=True,
        )
        self.assertFalse(out["market_truth_required"])
        self.assertFalse(out["active_targets_present"])

    def test_guard_requirements_enable_market_truth_for_active_targets(self):
        status_row = {
            "lifecycle_phase": "prepare",
            "active_targets_present": True,
            "market_truth_required": True,
        }
        out = resolve_guard_connectivity_requirements(
            status_row=status_row,
            require_book_feed_connected_config=True,
        )
        self.assertTrue(out["market_truth_required"])
        self.assertTrue(out["active_targets_present"])

    def test_classify_runtime_valid_scan(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "scan",
                "active_targets_present": False,
                "market_truth_required": False,
                "kill_switch": False,
                "external_guard_active": False,
            },
            {
                "ts_utc": "2099-01-01T00:05:00Z",
                "lifecycle_phase": "scan",
                "active_targets_present": False,
                "market_truth_required": False,
                "kill_switch": False,
                "external_guard_active": False,
            },
        ]
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_VALID_SCAN)
        self.assertFalse(out["promotion_eligible"])

    def test_classify_runtime_non_promotable_long_scan(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "scan",
                "active_targets_present": False,
                "market_truth_required": False,
                "kill_switch": False,
                "external_guard_active": False,
            },
            {
                "ts_utc": "2099-01-01T00:30:00Z",
                "lifecycle_phase": "scan",
                "active_targets_present": False,
                "market_truth_required": False,
                "kill_switch": False,
                "external_guard_active": False,
            },
        ]
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION)
        self.assertFalse(out["promotion_eligible"])

    def test_classify_runtime_invalid_deadlock(self):
        status_rows = []
        for idx in range(3):
            status_rows.append(
                {
                    "ts_utc": f"2099-01-01T00:0{idx}:00Z",
                    "lifecycle_phase": "scan",
                    "active_targets_present": False,
                    "market_truth_required": False,
                    "kill_switch": True,
                    "external_guard_active": True,
                }
            )
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_INVALID_DEADLOCK)
        self.assertFalse(out["promotion_eligible"])

    def test_classify_runtime_invalid_safety_active_targets_missing_required_feed(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "book_feed": {"enabled": True, "connected": False, "last_msg_age_sec": 45.0},
            }
        ]
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_INVALID_SAFETY)
        self.assertFalse(out["promotion_eligible"])

    def test_classify_runtime_single_unknown_age_disconnect_not_immediate_invalid_safety(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "book_feed": {"enabled": True, "connected": False, "last_msg_age_sec": None},
            }
        ]
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertNotEqual(out["classification"], RUNTIME_CLASS_INVALID_SAFETY)
        self.assertEqual(out["classification"], RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION)
        self.assertFalse(out["promotion_eligible"])

    def test_classify_runtime_sustained_unknown_age_disconnect_is_invalid_safety(self):
        status_rows = []
        for idx in range(3):
            status_rows.append(
                {
                    "ts_utc": f"2099-01-01T00:0{idx}:00Z",
                    "lifecycle_phase": "prepare",
                    "active_targets_present": True,
                    "market_truth_required": True,
                    "kill_switch": False,
                    "external_guard_active": False,
                    "book_feed": {"enabled": True, "connected": False, "last_msg_age_sec": None},
                }
            )
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_INVALID_SAFETY)
        self.assertFalse(out["promotion_eligible"])

    def test_classify_runtime_intermittent_unknown_age_disconnect_with_recovery_is_not_invalid_safety(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "book_feed": {"enabled": True, "connected": False, "last_msg_age_sec": None},
            },
            {
                "ts_utc": "2099-01-01T00:01:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "gauge.actions_last_cycle": 1,
                "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.4},
            },
            {
                "ts_utc": "2099-01-01T00:02:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "book_feed": {"enabled": True, "connected": False, "last_msg_age_sec": None},
            },
            {
                "ts_utc": "2099-01-01T00:03:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "gauge.open_orders": 1,
                "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.6},
            },
            {
                "ts_utc": "2099-01-01T00:04:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "book_feed": {"enabled": True, "connected": False, "last_msg_age_sec": None},
            },
        ]
        events = [{"event_type": "targets_updated"}, {"event_type": "risk_reject"}]
        out = classify_runtime(status_rows=status_rows, events=events)
        self.assertEqual(out["classification"], RUNTIME_CLASS_VALID_ACTIVE)
        self.assertTrue(out["promotion_eligible"])

    def test_classify_runtime_invalid_when_scan_has_order_submission_attempts(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "scan",
                "active_targets_present": False,
                "market_truth_required": False,
                "kill_switch": False,
                "external_guard_active": False,
                "gauge.order_submission_attempts_last_cycle": 1,
            }
        ]
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_INVALID_DEADLOCK)
        self.assertFalse(out["promotion_eligible"])

    def test_classify_runtime_invalid_when_scan_has_order_submission_attempts_in_status_window(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "scan",
                "active_targets_present": False,
                "market_truth_required": False,
                "kill_switch": False,
                "external_guard_active": False,
                "gauge.order_submission_attempts_last_status_window": 2,
            }
        ]
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_VALID_SCAN)
        self.assertFalse(out["promotion_eligible"])

    def test_classify_runtime_invalid_when_scan_requires_market_truth(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "scan",
                "active_targets_present": False,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "book_feed": {"enabled": True, "connected": False, "last_msg_age_sec": 45.0},
            }
        ]
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_INVALID_DEADLOCK)
        self.assertFalse(out["promotion_eligible"])

    def test_classify_runtime_valid_active(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "gauge.open_orders": 1,
                "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.2},
            },
            {
                "ts_utc": "2099-01-01T00:01:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "gauge.open_orders": 0,
                "gauge.actions_last_cycle": 2,
                "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.3},
            },
        ]
        events = [{"event_type": "risk_reject"}, {"event_type": "targets_updated"}]
        out = classify_runtime(status_rows=status_rows, events=events)
        self.assertEqual(out["classification"], RUNTIME_CLASS_VALID_ACTIVE)
        self.assertTrue(out["promotion_eligible"])

    def test_classify_runtime_taker_quick_read_counts_as_participation(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "gauge.taker_actions_last_cycle": 1,
                "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.3},
            }
        ]
        out = classify_runtime(status_rows=status_rows, events=[{"event_type": "targets_updated"}])
        self.assertEqual(out["classification"], RUNTIME_CLASS_VALID_ACTIVE)
        self.assertTrue(out["promotion_eligible"])

    def test_classify_runtime_taker_status_window_counts_as_participation(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "gauge.taker_submitted_last_status_window": 3,
                "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.3},
            }
        ]
        out = classify_runtime(status_rows=status_rows, events=[{"event_type": "targets_updated"}])
        self.assertEqual(out["classification"], RUNTIME_CLASS_VALID_ACTIVE)
        self.assertTrue(out["promotion_eligible"])

    def test_classify_runtime_non_promotable_when_active_targets_only_have_control_plane_events(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.2},
            },
            {
                "ts_utc": "2099-01-01T00:01:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": False,
                "external_guard_active": False,
                "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.3},
            },
        ]
        events = [{"event_type": "targets_updated"}, {"event_type": "targets_refreshed"}]
        out = classify_runtime(status_rows=status_rows, events=events)
        self.assertEqual(out["classification"], RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION)
        self.assertFalse(out["promotion_eligible"])

    def test_classify_runtime_emits_primary_suppression_cause_when_unique(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "scan",
                "active_targets_present": False,
                "market_truth_required": False,
                "kill_switch": False,
                "external_guard_active": False,
                "gauge.order_submission_attempts_last_cycle": 1,
            }
        ]
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_INVALID_DEADLOCK)
        self.assertEqual(out.get("primary_suppression_cause"), "scan_order_submission_violation")
        self.assertFalse(bool(out.get("ambiguous_suppression_cause", False)))
        self.assertIn("scan_order_submission_violation", out.get("suppression_cause_candidates", []))

    def test_classify_runtime_emits_ambiguous_suppression_cause_on_tied_precedence(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "kill_switch": True,
                "external_guard_active": False,
                "book_feed": {"enabled": True, "connected": False, "last_msg_age_sec": 45.0},
            }
        ]
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_INVALID_SAFETY)
        self.assertTrue(bool(out.get("ambiguous_suppression_cause", False)))
        self.assertEqual(str(out.get("primary_suppression_cause") or ""), "")
        contributing = set(out.get("contributing_suppression_causes", []))
        self.assertIn("safety_kill_switch_or_external_guard", contributing)
        self.assertIn("safety_required_market_truth_disconnected", contributing)

    def test_classify_runtime_status_rows_missing_emits_explicit_primary_cause(self):
        out = classify_runtime(status_rows=[], events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_INVALID_DEADLOCK)
        self.assertEqual(out.get("primary_suppression_cause"), "status_rows_missing")
        self.assertFalse(bool(out.get("ambiguous_suppression_cause", False)))

    def test_classify_runtime_runtime_state_ambiguous_emits_explicit_primary_cause(self):
        status_rows = [
            {
                "ts_utc": "2099-01-01T00:00:00Z",
                "runtime_state": "active",
                "active_targets_present": False,
                "market_truth_required": False,
                "kill_switch": False,
                "external_guard_active": False,
            }
        ]
        out = classify_runtime(status_rows=status_rows, events=[])
        self.assertEqual(out["classification"], RUNTIME_CLASS_INVALID_DEADLOCK)
        self.assertIn("runtime_state_ambiguous", out.get("reasons", []))
        self.assertEqual(out.get("primary_suppression_cause"), "runtime_state_ambiguous")
        self.assertFalse(bool(out.get("ambiguous_suppression_cause", False)))


if __name__ == "__main__":
    unittest.main()
