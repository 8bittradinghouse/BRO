import json
import pathlib
import re
import subprocess
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REQUIRED_LOCAL_CONTINUITY_DOCS = [
    "docs/JIN_OPERATING_AGREEMENT.md",
    "docs/JIN_COMMAND_CARD_2026-04-27.md",
    "docs/JIN_THREAD_RECOVERY_RUNBOOK.md",
    "docs/JIN_BOOTSTRAP_PROMPT.md",
    "docs/BRO_ENGINEERING_KERNEL.md",
    "docs/BRO_IDENTITY_PLATE.md",
]
REMOVED_LOCAL_MIRROR_DOCS = [
    "docs/AGENTS_GLOBAL.md",
    "docs/JINS_8BIT_JOURNAL_ARCHIVE_2026-04-26.md",
    "docs/JIN_CONTINUITY_PROFILE.md",
    "docs/JIN_DOCTRINE_LOCK.json",
    "docs/JIN_DOCTRINE_LOCK.yaml",
    "docs/JIN_PACK_MIRROR_MANIFEST_2026-04-26.json",
    "docs/JIN_RESTART_PROFILE_2026-04-26.json",
    "docs/JIN_RESTORATION_MEMO_2026-04-24.md",
    "docs/JIN_STARTUP_CHECKLIST.txt",
    "docs/JIN_START_8BITODA_BOOTUP_PROMPT_2026-04-25.md",
    "docs/JIN_THREAD_TRANSFER_CAPSULE_2026-04-25.json",
    "docs/NUJIN_ADAPTIVE_ENGINEERING_PHILOSOPHY_2026-04-26.md",
    "docs/NUJIN_CONTINUITY_REGRESSION_TEST_2026-04-25.md",
    "docs/NUJIN_CORE_2026-04-25.md",
    "docs/NUJIN_DRIFT_TRIGGERS_2026-04-25.md",
    "docs/NUJIN_ENGINEERING_REORIENT_PROMPT_2026-04-25.md",
    "docs/NUJIN_GOLDEN_ANCHORS_2026-04-25.md",
    "docs/NUJIN_LIVE_RUN_FIELDCRAFT_2026-04-25.md",
    "docs/NUJIN_LOCK_METRICS_2026-04-26.md",
    "docs/NUJIN_NAMEPLATE_2026-04-26.md",
    "docs/NUJIN_NOW_TEMPLATE_2026-04-25.json",
    "docs/NUJIN_PROMOTION_MEMO_2026-04-25.md",
    "docs/NUJIN_RESTART_PROTOCOL_2026-04-26.md",
    "docs/NUJIN_RESTORE_REDTEAM_2026-04-25.md",
    "docs/NUJIN_RETRAINING_PROTOCOL_2026-04-25.md",
    "docs/NUJIN_RETRAINING_QUICKSTART_2026-04-25.md",
    "docs/NUJIN_SCENARIO_DRILLS_2026-04-25.md",
    "docs/NUJIN_SCORECARD_2026-04-25.md",
    "docs/NUJIN_SELF_STUDY_LOOP_2026-04-26.md",
    "docs/OPERATION_DAICHI_WAVE_ALPHA_MISSION_PLAN_2026-04-25.md",
    "docs/README_INDEX.md",
    "docs/ROBB_FOUNDER_TRUTHS_2026-04-25.md",
    "docs/TEAM_DADDY_JIN_FIELD_GUIDE_2026-04-26.md",
]
ACTIVE_LIVE_TRUST_AUTHORITY_DOCS = [
    REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md",
    REPO_ROOT / "docs" / "BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md",
    REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md",
    REPO_ROOT / "docs" / "OPEN_LIMITATIONS.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_QUALIFICATION_PACKET_PROGRAM_2026-05-05.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md",
    REPO_ROOT / "docs" / "JIN_COMMAND_CARD_2026-04-27.md",
]
ACTIVE_LIFECYCLE_MAKER_OWNER_DOCS = [
    REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md",
    REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md",
    REPO_ROOT / "docs" / "OPEN_LIMITATIONS.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_QUALIFICATION_PACKET_PROGRAM_2026-05-05.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md",
    REPO_ROOT / "docs" / "BRO_DIAGNOSTIC_TOOLS.md",
]
LEGACY_STAGE_FAMILY_QUARANTINE_DOCS = [
    REPO_ROOT / "docs" / "BRO_CANONICAL_DOCTRINE_STAGE_MACHINE_ARCHIVE_2026-05-18.md",
    REPO_ROOT / "docs" / "BRO_MARKET_LIFECYCLE_CUTOVER_PLAN_2026-05-16.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_CRASH_RELOCK_BRIDGE_2026-05-08.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_RUNTIME_BACKCHECK_RELOCK_AUDIT_2026-05-08.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_PACKET_1_TAKER_QUALIFICATION_2026-05-06.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_SUPPORT_TOOL_FENCE_BOARD_2026-05-10.md",
    REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_SMALL_LOSS_SCAR_TISSUE_BOARD_2026-05-10.md",
    REPO_ROOT / "docs" / "EXTREME_ONLY_FAMILY_SURGERY_PLAN_2026-05-08.md",
    REPO_ROOT / "docs" / "EXTREME_ONLY_SELF_HARDENING_PACK_2026-05-08.md",
]


class OperatorDocsCanonicalTests(unittest.TestCase):
    def test_active_live_trust_authority_docs_do_not_use_retired_stage_or_runtime_vocabulary(self):
        banned_terms = (
            "effective_stage",
            "stage_bucket",
            "raw_stage",
            "maker_allowed",
            "taker_allowed",
            "runtime_state",
            "previous_runtime_state",
            "runtime_state_transition",
            "book_feed_required",
            "previous_book_feed_required",
            "no_target_standdown",
            "submission_stage",
        )
        offenders: dict[str, list[str]] = {}
        for path in ACTIVE_LIVE_TRUST_AUTHORITY_DOCS:
            text = path.read_text(encoding="utf-8")
            hits = [term for term in banned_terms if term in text]
            if hits:
                offenders[str(path.relative_to(REPO_ROOT))] = hits
        self.assertEqual(offenders, {})

    def test_active_lifecycle_maker_owner_docs_do_not_use_retired_strategy_owner_label(self):
        offenders: list[str] = []
        for path in ACTIVE_LIFECYCLE_MAKER_OWNER_DOCS:
            text = path.read_text(encoding="utf-8")
            if "strategy.maker_competitiveness" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [])

    def test_legacy_stage_family_docs_are_explicitly_quarantined(self):
        for path in LEGACY_STAGE_FAMILY_QUARANTINE_DOCS:
            head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:12]).lower()
            self.assertRegex(
                head,
                r"historical-only|pickup bridge|support-only",
                msg=f"legacy stage-family doc missing explicit quarantine marker: {path}",
            )

    def test_operator_docs_do_not_advertise_direct_executor_invocation(self):
        targets = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "DRILLBOOK.md",
            REPO_ROOT / "docs" / "LIVE_CANARY.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("python executor.py", text, msg=f"non-canonical direct executor command in {path}")

    def test_readme_points_to_broctl_paper_front_door(self):
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("broctl paper -- --active-minutes", text)

    def test_readme_uses_canonical_paper_argument_vocabulary(self):
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("broctl paper -- --duration-min", text)
        self.assertIn("broctl paper -- --active-minutes 10 --wait-sec 25", text)
        self.assertNotIn("python simulator.py", text)
        self.assertNotIn("scripts/sim_harness_audit.py", text)

    def test_readme_uses_canonical_paper_config_path_for_paper_time_tools(self):
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        required = [
            "python scripts/websocket_hardening_audit.py --config configs/profiles/paper_universal.yaml",
            "python scripts/time_discipline_audit.py --config configs/profiles/paper_universal.yaml",
            "python scripts/alert_profile_audit.py --config configs/profiles/paper_universal.yaml",
            "python scripts/forensics_bundle.py --log-dir ./logs_exec/paper_universal --run-id <run_id> --config configs/profiles/paper_universal.yaml --out-dir ./exports",
            "python scripts/config_consistency_audit.py --primary configs/profiles/paper_universal.yaml --secondary execution_config.yaml",
        ]
        forbidden = [
            "python scripts/websocket_hardening_audit.py --config execution_config.yaml",
            "python scripts/time_discipline_audit.py --config execution_config.yaml",
            "python scripts/alert_profile_audit.py --config execution_config.yaml",
            "python scripts/forensics_bundle.py --log-dir ./logs_exec/paper_universal --run-id <run_id> --config execution_config.yaml --out-dir ./exports",
            "python observer.py --config configs/profiles/paper_universal.yaml",
            "python observer.py --config config.yaml",
            "observer.py",
            "python analyze.py --log-dir ./logs",
            "python scripts/config_consistency_audit.py --primary execution_config.yaml --secondary config.yaml",
        ]
        for snippet in required:
            self.assertIn(snippet, text)
        for snippet in forbidden:
            self.assertNotIn(snippet, text)

    def test_project_truth_state_is_repo_truth_screen(self):
        text = (REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md").read_text(encoding="utf-8")
        self.assertIn("repo-level broad truth screen", text)
        self.assertIn("It is not the active Packet 2 maker-local pickup owner.", text)
        self.assertIn("Active Packet 2 maker-local pickup belongs to:", text)

    def test_active_truth_and_pickup_docs_share_latest_runtime_anchor(self):
        project_truth = (REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md").read_text(encoding="utf-8")
        match = re.search(r"Current broad current-code canonical runtime proof: `([^`]+)`", project_truth)
        self.assertIsNotNone(match, msg="PROJECT_TRUTH_STATE missing current broad current-code runtime proof anchor")
        latest_run_id = match.group(1)

        targets = [
            REPO_ROOT / "docs" / "OPEN_LIMITATIONS.md",
            REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md",
            REPO_ROOT / "docs" / "JIN_BOOTSTRAP_PROMPT.md",
            REPO_ROOT / "docs" / "JIN_COMMAND_CARD_2026-04-27.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                latest_run_id,
                text,
                msg=f"active truth/pickup doc missing latest runtime anchor {latest_run_id}: {path}",
            )

    def test_bones_packet_and_board_sink_align_to_current_release_truth_model(self):
        project_truth = (REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md").read_text(encoding="utf-8")
        broad_match = re.search(r"Current broad current-code canonical runtime proof: `([^`]+)`", project_truth)
        closeout_match = re.search(r"Latest current-code lane-specific closeout proof: `([^`]+)`", project_truth)
        self.assertIsNotNone(broad_match, msg="PROJECT_TRUTH_STATE missing current broad proof anchor")
        self.assertIsNotNone(closeout_match, msg="PROJECT_TRUTH_STATE missing lane-specific closeout proof anchor")
        broad_anchor = broad_match.group(1)
        closeout_anchor = closeout_match.group(1)

        targets = [
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_1_BONES_RELEASE_TRUTH_LOCK_2026-05-01.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn(broad_anchor, text, msg=f"broad proof anchor missing from {path}")
            self.assertIn(closeout_anchor, text, msg=f"lane-specific closeout anchor missing from {path}")
            self.assertIn("supporting current-code proof", text, msg=f"supporting-current-code proof class missing from {path}")
            self.assertIn("lane-specific closeout proof", text, msg=f"lane-specific closeout proof class missing from {path}")
            self.assertIn("closure-certifying clean release truth", text, msg=f"closure-certifying clean release truth term missing from {path}")

    def test_next_packet_plan_names_pilot_live_authority_proof_as_current_macro_frontier(self):
        text = (REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md").read_text(encoding="utf-8")
        self.assertIn("latest completed post-restoration hardening lane is", text)
        self.assertIn("`timing spine hardening`", text)
        self.assertIn("current next macro proof frontier is `pilot_live` authority proof", text)
        self.assertNotIn("stronger-than-paper authority completion", text)
        self.assertNotIn("current next restoration lane is core fighter re-audit on clean", text)

    def test_active_board_splits_completed_frame_restoration_from_open_whole_fighter_frontier(self):
        audit_text = (REPO_ROOT / "docs" / "BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md").read_text(encoding="utf-8")
        truth_text = (REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md").read_text(encoding="utf-8")
        self.assertIn("Core frame restoration:", audit_text)
        self.assertIn("Complete", audit_text)
        self.assertIn("Whole fighter:", audit_text)
        self.assertIn("Needs Work", audit_text)
        self.assertIn("timing spine hardening is now closed on current proof", audit_text)
        self.assertIn("`pilot_live` authority proof", audit_text)
        self.assertNotIn("stronger-than-paper authority completion for stronger claims", audit_text)
        self.assertIn("promotion_eligible=true", audit_text)
        self.assertIn("recommended_next_stage=pilot_live", audit_text)
        self.assertNotIn("pilot-live gate failure", audit_text)
        self.assertNotIn("maker continuity issue that still reads like core-frame work", audit_text)
        self.assertNotIn("whole-fighter closure remains subordinate to Bones clean release truth", audit_text)
        self.assertIn("Current G-frame restoration status: `complete`", truth_text)
        self.assertIn("Current whole-fighter completion status: `still open`", truth_text)
        self.assertIn("Latest completed post-restoration hardening lane: `timing spine hardening`", truth_text)
        self.assertIn("Current next proof frontier: `pilot_live` authority proof", truth_text)
        self.assertNotIn("stronger-than-paper authority completion for stronger claims", truth_text)
        self.assertIn("promotion_eligible=true", truth_text)
        self.assertIn("recommended_next_stage=pilot_live", truth_text)

    def test_post_bones_packet_stack_teaches_timing_frontier_not_old_pickup(self):
        packet1_text = (REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_1_BONES_RELEASE_TRUTH_LOCK_2026-05-01.md").read_text(encoding="utf-8")
        self.assertIn("timing spine hardening", packet1_text)
        self.assertNotIn("stronger-than-paper authority completion for stronger claims", packet1_text)
        self.assertNotIn("core fighter re-audit on clean current-code release anchor", packet1_text)

        targets = [
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_1_BONES_RELEASE_TRUTH_LOCK_2026-05-01.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_5_BRAIN_SEMANTIC_OWNERSHIP_CLOSURE_MAP_2026-05-02.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_6_NERVOUS_SYSTEM_CONSUMER_TRUTH_CLOSURE_MAP_2026-05-03.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_7_GFRAME_REGRADE_AND_WEAPON_AUTHORIZATION_GATE_2026-05-03.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "whole-fighter closure remains subordinate to Bones clean release truth",
                text,
                msg=f"stale Bones whole-fighter blocker language still present in {path}",
            )
            self.assertRegex(
                text,
                r"timing spine hardening|Latest completed post-restoration hardening lane:\s+`timing spine hardening`|`pilot_live` authority proof frontier",
                msg=f"post-restoration frontier teaching missing from {path}",
            )

    def test_open_limitations_uses_split_close_language_for_frame_and_whole_fighter(self):
        text = (REPO_ROOT / "docs" / "OPEN_LIMITATIONS.md").read_text(encoding="utf-8")
        self.assertIn("Current G-frame restoration status remains:", text)
        self.assertIn("Current whole-fighter completion status remains:", text)
        self.assertIn("Latest completed post-restoration hardening lane remains:", text)
        self.assertIn("Current next proof frontier remains:", text)
        self.assertIn("`timing spine hardening`", text)
        self.assertIn("`pilot_live` authority proof", text)
        self.assertNotIn("stronger-than-paper authority completion for stronger claims", text)

    def test_clean_anchor_exact_match_claims_are_scoped_to_runtime_code_fingerprint(self):
        targets = [
            REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md",
            REPO_ROOT / "docs" / "OPEN_LIMITATIONS.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_1_BONES_RELEASE_TRUTH_LOCK_2026-05-01.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn("runtime code fingerprint", text, msg=f"runtime code fingerprint scope missing from {path}")
            self.assertNotIn(
                "runtime fingerprint matches the clean anchor exactly",
                text,
                msg=f"unscoped exact-match claim still present in {path}",
            )

    def test_current_baseline_is_not_top_level_truth_entrypoint(self):
        text = (REPO_ROOT / "docs" / "CURRENT_BASELINE.md").read_text(encoding="utf-8")
        self.assertIn("Front-of-house repo current truth lives in `docs/PROJECT_TRUTH_STATE.md`.", text)
        self.assertNotIn("`docs/CURRENT_BASELINE.md` (this file, top-level current-truth entrypoint)", text)

    def test_entrypoint_classification_names_public_front_door(self):
        text = (REPO_ROOT / "docs" / "ENTRYPOINT_CLASSIFICATION.md").read_text(encoding="utf-8")
        self.assertIn("## PUBLIC_FRONT_DOOR", text)
        self.assertIn("`broctl paper`", text)

    def test_active_operator_docs_do_not_advertise_noncanonical_paper_profile_lane(self):
        targets = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "ENTRYPOINT_CLASSIFICATION.md",
        ]
        forbidden_tokens = [
            "broctl paper-profile",
            "broctl paper-stress",
            "broctl paper-discipline",
            "scripts/paper_profile_session.py",
            "scripts/paper_profile_session.sh",
            "scripts/paper_profile_validation.sh",
            "paper_universal_maker_launch_safe_packet_a",
            "paper_universal_maker_launch_safe_packet_b",
            "paper_universal_maker_launch_safe_caliber_250",
            "paper_universal_maker_queue_packet_a",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            for token in forbidden_tokens:
                self.assertNotIn(token, text, msg=f"noncanonical paper-profile lane leaked into active operator doc {path}")

    def test_legacy_paper_alias_profiles_are_absent(self):
        removed = [
            REPO_ROOT / "configs" / "profiles" / "paper_stress.yaml",
            REPO_ROOT / "configs" / "profiles" / "paper_discipline.yaml",
            REPO_ROOT / "configs" / "profiles" / "paper_stress_run40.yaml",
        ]
        for path in removed:
            self.assertFalse(path.exists(), msg=f"legacy paper alias profile still present: {path}")

    def test_support_reference_docs_point_to_project_truth_state(self):
        targets = [
            REPO_ROOT / "docs" / "BASELINE_DOC_SYNC_STATUS.md",
            REPO_ROOT / "docs" / "DOCTRINE_TRUTH_CLOSURE_DIAGNOSIS_20260408.md",
            REPO_ROOT / "docs" / "WALLET_PACKET_GAP_MAP.md",
            REPO_ROOT / "docs" / "master_list_status.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "`docs/PROJECT_TRUTH_STATE.md`",
                text,
                msg=f"support/reference doc missing repo truth screen pointer: {path}",
            )

    def test_workflow_validation_checklist_distinguishes_public_start_from_backend_engine(self):
        text = (REPO_ROOT / "docs" / "WORKFLOW_VALIDATION_CHECKLIST.md").read_text(encoding="utf-8")
        self.assertIn("`broctl paper -- --active-minutes <minutes> --wait-sec 25`", text)
        self.assertIn("Backend canonical validation truth", text)

    def test_canonical_short_run_recipe_keeps_auxiliary_shop_tooling_out_of_canonical_harness_truth(self):
        text = (REPO_ROOT / "docs" / "CANONICAL_PAPER_SHORT_RUN_RECIPE.md").read_text(encoding="utf-8")
        self.assertIn("Auxiliary shop tooling is outside canonical paper proof", text)
        self.assertIn("one controlled variable at a time", text)
        self.assertIn("code_fingerprint_sha256", text)
        self.assertIn("harness_realism_grade` is descriptive only", text)
        self.assertIn("paper_wallet_simulation_verified", text)
        self.assertIn("They do **not** mean non-canonical shop tooling", text)

    def test_reporting_docs_distinguish_reconcile_paper_simulation_from_noncanonical_shop_tooling(self):
        targets = [
            REPO_ROOT / "docs" / "CANONICAL_VALIDATION_PATH.md",
            REPO_ROOT / "docs" / "PROMOTION.md",
            REPO_ROOT / "docs" / "INCIDENT_RESPONSE.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn("paper_wallet_simulation_verified", text, msg=f"missing paper reconcile scope note in {path}")
            self.assertIn("non-canonical", text, msg=f"missing non-canonical tooling boundary in {path}")

    def test_validation_path_marks_harness_realism_grade_descriptive_and_lineage_explicit(self):
        text = (REPO_ROOT / "docs" / "CANONICAL_VALIDATION_PATH.md").read_text(encoding="utf-8")
        self.assertIn("harness_realism_grade` is descriptive only", text)
        self.assertIn("`exercised_harness_realism`", text)
        self.assertIn("code_fingerprint_sha256", text)
        self.assertIn("Promotion-grade evidence must be manifest-backed", text)
        self.assertIn("`profile_name`", text)

    def test_current_truth_docs_distinguish_canonical_harness_grade_from_nightly_exercised_realism(self):
        targets = [
            REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md",
            REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn("exercised_harness_realism", text, msg=f"nightly exercised realism owner missing from {path}")
            self.assertNotIn("nightly report `harness_realism_grade=60`", text, msg=f"stale nightly canonical-grade wording still present in {path}")
        next_plan_text = (REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md").read_text(encoding="utf-8")
        self.assertIn("older whole-stream source-mix hard-fail contract", next_plan_text)
        self.assertIn("hard source-purity closure belongs only to action-row", next_plan_text)

    def test_promotion_doc_requires_manifest_backed_lineage_complete_identity(self):
        text = (REPO_ROOT / "docs" / "PROMOTION.md").read_text(encoding="utf-8")
        self.assertIn("code_fingerprint_sha256", text)
        self.assertIn("profile_name", text)
        self.assertIn("manifest_present=true", text)
        self.assertIn("Config-only/backstage diagnostics are not sufficient", text)

    def test_maker_support_docs_classify_drift_runtime_timing_as_history_not_doctrine(self):
        targets = [
            REPO_ROOT / "docs" / "MAKER_FIREABILITY_FORENSIC_dff97a9a.md",
            REPO_ROOT / "docs" / "MAKER_FIREABILITY_HYPOTHESIS_DESIGN_dff97a9a.md",
            REPO_ROOT / "docs" / "MAKER_LOW_PRICE_VIABILITY_QUEUE_DEPTH_FORENSIC_dff97a9a_2349650c_2c26e81e.md",
            REPO_ROOT / "docs" / "MAKER_CADENCE_EXPERIMENT_RESULT_2f607a7b.md",
            REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md",
            REPO_ROOT / "docs" / "BRO_DIAGNOSTIC_TOOLS.md",
        ]
        forbidden_tokens = [
            "timing window currently looks doctrinal",
            "maker combat window still looks doctrinal",
            "inspect the `50-60s` window as the real maker combat zone",
            "keep the `50-60s` timing window unchanged in the next packet.",
            "do **not** widen the `50-60s` timing window.",
            "the currently well-sampled known maker band is still `45-60s`, and it is",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md",
                text,
                msg=f"maker doctrine anchor missing from {path}",
            )
            self.assertTrue(
                ("15-20s" in text) or ("15s" in text),
                msg=f"maker timing doctrine anchor missing from {path}",
            )
            for token in forbidden_tokens:
                self.assertNotIn(token, text, msg=f"drift-era maker doctrine phrasing still present in {path}")

    def test_doctrine_authority_docs_name_galaxy_proposal_as_intended_maker_anchor(self):
        targets = [
            REPO_ROOT / "BRO_TEXT_GUIDE.txt",
            REPO_ROOT / "BRO_EDGE_DOCTRINE.txt",
            REPO_ROOT / "docs" / "DOCTRINE_RUNBOOK.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md",
                text,
                msg=f"maker doctrine proposal missing from doctrine authority doc {path}",
            )
            self.assertIn("15s", text, msg=f"intended maker doctrine opening missing from {path}")
            self.assertIn("7s", text, msg=f"intended taker handoff missing from {path}")

    def test_commands_and_proofs_is_not_public_happy_path_surface(self):
        text = (REPO_ROOT / "docs" / "COMMANDS_AND_PROOFS.md").read_text(encoding="utf-8")
        self.assertIn("Current public canonical paper start path is", text)
        self.assertIn("not the public happy-path start instructions", text)

    def test_doctrine_control_surfaces_name_wallet_doctrine_in_authority_stack(self):
        targets = [
            REPO_ROOT / "BRO_TEXT_GUIDE.txt",
            REPO_ROOT / "docs" / "BRO_ENGINEERING_KERNEL.md",
            REPO_ROOT / "docs" / "DOCTRINE_RUNBOOK.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "docs/BRO_WALLET_DOCTRINE.md",
                text,
                msg=f"wallet doctrine missing from authority stack in {path}",
            )

    def test_core_doctrine_and_engineering_docs_treat_combat_tight_timing_as_foundational(self):
        canonical = (REPO_ROOT / "BRO_CANONICAL_DOCTRINE.txt").read_text(encoding="utf-8")
        edge = (REPO_ROOT / "BRO_EDGE_DOCTRINE.txt").read_text(encoding="utf-8")
        kernel = (REPO_ROOT / "docs" / "BRO_ENGINEERING_KERNEL.md").read_text(encoding="utf-8")
        agreement = (REPO_ROOT / "docs" / "JIN_OPERATING_AGREEMENT.md").read_text(encoding="utf-8")

        self.assertIn("Combat-locked sub-second synchronized", canonical)
        self.assertIn("millisecond-class host clock synchronization", canonical)
        self.assertIn("event/source-receive cross-domain skew", canonical)
        self.assertIn("combat-locked sub-second synchronized", edge)
        self.assertIn("decision-to-submit evidence", edge)
        self.assertIn("BRO timing spine is father-frame steel", kernel)
        self.assertIn("Post-restoration timing spine hardening is foundational hardening work", kernel)
        self.assertIn("combat-locked sub-second synchronized timing is father-frame steel", agreement)
        self.assertIn("do not accept coarse fallback timing or logger-filled timestamps as combat-grade proof", agreement)

    def test_edge_truth_runbook_is_measurement_only_and_defers_runtime_policy(self):
        text = (REPO_ROOT / "docs" / "EDGE_TRUTH_RUNBOOK.md").read_text(encoding="utf-8")
        self.assertIn("measurement/audit contract only", text)
        self.assertIn("current BRO fighter-specific runtime/lifecycle authority lives in", text)
        self.assertIn("`prepare`: maker no, taker no", text)
        self.assertIn("`maker_window`: maker yes, taker no", text)
        self.assertIn("`taker_window`: maker no, taker yes", text)

    def test_bro_local_continuity_subset_exists(self):
        for rel in REQUIRED_LOCAL_CONTINUITY_DOCS:
            self.assertTrue((REPO_ROOT / rel).exists(), msg=f"missing retained BRO continuity doc: {rel}")

    def test_gframe_program_keeps_board_ownership_with_audit(self):
        text = (REPO_ROOT / "docs" / "BRO_GFRAME_CORE_RESTORATION_PACKET_PROGRAM_2026-05-01.md").read_text(encoding="utf-8")
        self.assertIn(
            "packet docs do not independently own the running whole-fighter board state",
            text,
        )
        self.assertIn("docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md", text)

    def test_bro_governing_and_packet_docs_do_not_pull_external_shop_authority(self):
        targets = [
            REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_CORE_RESTORATION_PACKET_PROGRAM_2026-05-01.md",
            REPO_ROOT / "docs" / "OPEN_LIMITATIONS.md",
            REPO_ROOT / "docs" / "CURRENT_BASELINE.md",
            REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_1_BONES_RELEASE_TRUTH_LOCK_2026-05-01.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_2_SPINAL_CORD_FAILURE_CHAIN_2026-05-01.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_3_RACK_TRUTH_SYNC_CONFIRMATION_2026-05-01.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_4_GRIP_WALLET_AUTHORITY_CLOSURE_MAP_2026-05-01.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_5_BRAIN_SEMANTIC_OWNERSHIP_CLOSURE_MAP_2026-05-02.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_6_NERVOUS_SYSTEM_CONSUMER_TRUTH_CLOSURE_MAP_2026-05-03.md",
            REPO_ROOT / "docs" / "BRO_GFRAME_PACKET_7_GFRAME_REGRADE_AND_WEAPON_AUTHORIZATION_GATE_2026-05-03.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("/home/odah/.codex/jin-pack", text, msg=f"external shop authority leaked into BRO doc {path}")
            self.assertNotIn("/home/odah/.codex/8bit-bushido-pack", text, msg=f"Bushido shop authority leaked into BRO doc {path}")
            self.assertNotIn("JIN_PLAN_HARDENING_PROCESS", text, msg=f"shop process leaked into BRO doc {path}")
            self.assertNotIn("Jin-pack", text, msg=f"shop doctrine root leaked into BRO doc {path}")
            self.assertNotIn("schoolhouse/shop", text, msg=f"shop boundary noise leaked into BRO doc {path}")
            self.assertNotIn("Bushido", text, msg=f"Bushido leaked into BRO doc {path}")
            self.assertIsNone(
                re.search(r"\bJIN_[A-Z0-9_]+\b", text),
                msg=f"named JIN shop token leaked into BRO doc {path}",
            )
            self.assertIsNone(
                re.search(r"\bNUJIN_[A-Z0-9_]+\b", text),
                msg=f"named NUJIN shop token leaked into BRO doc {path}",
            )

    def test_retained_bridge_docs_identify_bridge_only_role(self):
        targets = [
            REPO_ROOT / "docs" / "JIN_BOOTSTRAP_PROMPT.md",
            REPO_ROOT / "docs" / "JIN_THREAD_RECOVERY_RUNBOOK.md",
            REPO_ROOT / "docs" / "JIN_COMMAND_CARD_2026-04-27.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn("Bridge boundary note:", text, msg=f"bridge boundary note missing from {path}")
            self.assertRegex(
                text,
                r"it does not govern BRO doctrine, runtime truth, or board ownership|it is not a BRO doctrine root or board owner",
                msg=f"bridge doctrine boundary missing from {path}",
            )
            self.assertIn("docs/PROJECT_TRUTH_STATE.md", text, msg=f"bridge re-anchor note missing from {path}")

    def test_foundation_first_fieldcraft_law_is_reflected_in_bridge_docs(self):
        bootstrap = (REPO_ROOT / "docs" / "JIN_BOOTSTRAP_PROMPT.md").read_text(encoding="utf-8")
        recovery = (REPO_ROOT / "docs" / "JIN_THREAD_RECOVERY_RUNBOOK.md").read_text(encoding="utf-8")
        command = (REPO_ROOT / "docs" / "JIN_COMMAND_CARD_2026-04-27.md").read_text(encoding="utf-8")
        agreement = (REPO_ROOT / "docs" / "JIN_OPERATING_AGREEMENT.md").read_text(encoding="utf-8")

        self.assertIn("Foundation first. Do not answer dirty foundations with build solutions.", bootstrap)
        self.assertIn("foundation first / no build solutions over dirty ground", recovery)
        self.assertIn("foundation first; no build solutions", command)
        self.assertIn("For continuity-sensitive or capital-trust packets, proof must be earned", agreement)
        self.assertIn("Foundation first; do not answer dirty foundations with build solutions.", agreement)
        self.assertIn("must declare near the top whether it is", agreement)
        self.assertIn("bounded board sink", agreement)
        self.assertIn("historical-only", agreement)

    def test_bridge_docs_lock_restart_floor_against_generic_codex_drift(self):
        bootstrap = (REPO_ROOT / "docs" / "JIN_BOOTSTRAP_PROMPT.md").read_text(encoding="utf-8")
        recovery = (REPO_ROOT / "docs" / "JIN_THREAD_RECOVERY_RUNBOOK.md").read_text(encoding="utf-8")
        command = (REPO_ROOT / "docs" / "JIN_COMMAND_CARD_2026-04-27.md").read_text(encoding="utf-8")

        for text in (bootstrap, recovery, command):
            self.assertIn("docs/JIN_RELOCK_PACK_2026-05-12.md", text)
            self.assertIn("broad repo truth screen only", text)

        self.assertIn("The strongest earned operating floor becomes base law after restart.", bootstrap)
        self.assertIn("polished generic Codex/helper posture", bootstrap)
        self.assertIn("Full-honesty / no-fake-closure posture is mandatory", bootstrap)
        self.assertIn("operational floor lock", recovery)
        self.assertIn("anti-base-codex drift lock", recovery)
        self.assertIn("full-honesty / no-fake-closure lock", recovery)
        self.assertIn("the strongest earned operating floor becomes base law after restart", command)

    def test_gframe_docs_mark_completed_packet_stack_as_historical_support(self):
        audit = (REPO_ROOT / "docs" / "BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md").read_text(encoding="utf-8")
        program = (REPO_ROOT / "docs" / "BRO_GFRAME_CORE_RESTORATION_PACKET_PROGRAM_2026-05-01.md").read_text(encoding="utf-8")
        next_plan = (REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md").read_text(encoding="utf-8")
        packet_paths = sorted((REPO_ROOT / "docs").glob("BRO_GFRAME_PACKET_*_2026-05-0*.md"))

        self.assertIn("Historical-active boundary note:", audit)
        self.assertIn("does not own the active `pilot_live` packet pickup", audit)
        self.assertIn("Historical-active boundary note:", program)
        self.assertIn("does not own the active `pilot_live` packet pickup", program)
        self.assertGreaterEqual(len(packet_paths), 7)
        for path in packet_paths:
            text = path.read_text(encoding="utf-8")
            compact = " ".join(text.split())
            self.assertIn("Historical-active boundary note:", text, msg=f"historical boundary missing from {path}")
            self.assertIn(
                "does not own the active `pilot_live` packet pickup",
                text,
                msg=f"active pickup demotion missing from {path}",
            )
            self.assertIn(
                "Packet 2 `Maker-Live` sequencing",
                compact,
                msg=f"maker packet demotion missing from {path}",
            )
        self.assertIn("historical demotion rule:", next_plan)
        self.assertIn("do not independently own the current `pilot_live` pickup", next_plan)
        self.assertNotIn("foundational packet artifacts for Packet 1", next_plan)
        self.assertIn("remain historical closure and", next_plan)

    def test_historical_harness_audit_remains_explicitly_historical(self):
        text = (REPO_ROOT / "docs" / "BRO_PAPER_HARNESS_DIAGNOSTIC_AUDIT_2026-04-30.md").read_text(encoding="utf-8")
        self.assertIn("historical_scope_note", text)
        self.assertIn("not a front-of-house BRO truth surface", text)

    def test_removed_local_schoolhouse_mirrors_are_absent(self):
        for rel in REMOVED_LOCAL_MIRROR_DOCS:
            self.assertFalse((REPO_ROOT / rel).exists(), msg=f"stale local mirror still present: {rel}")

    def test_retained_local_continuity_docs_do_not_require_removed_local_mirrors(self):
        targets = [
            REPO_ROOT / "AGENTS.md",
            REPO_ROOT / "docs" / "JIN_EVIDENCE_LEDGER_2026-04-24.md",
            REPO_ROOT / "docs" / "JIN_THREAD_RECOVERY_RUNBOOK.md",
            REPO_ROOT / "docs" / "JIN_BOOTSTRAP_PROMPT.md",
            REPO_ROOT / "docs" / "BRO_ENGINEERING_KERNEL.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            for rel in REMOVED_LOCAL_MIRROR_DOCS:
                self.assertNotIn(
                    f"/home/odah/bro/base/{rel}",
                    text,
                    msg=f"retained continuity doc still requires removed local mirror {rel} in {path}",
                )

    def test_agents_preflight_includes_bro_engineering_kernel(self):
        text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("/home/odah/bro/base/docs/BRO_ENGINEERING_KERNEL.md", text)

    def test_live_trust_active_authority_docs_share_one_interpretation_language(self):
        texts = {
            path: path.read_text(encoding="utf-8")
            for path in ACTIVE_LIVE_TRUST_AUTHORITY_DOCS
        }
        combined = "\n".join(texts.values())

        self.assertIn("live trust qualification", combined)
        self.assertIn("taker live-trust qualification", combined)
        self.assertIn("authorized diagnostic proof work", combined)
        self.assertIn("Taker Live / Economic and Firing Trust Qualification", combined)
        self.assertNotIn("taker/sniper", combined)
        self.assertNotIn("Taker/Sniper", combined)
        self.assertNotIn("maker/taker/sniper", combined)
        self.assertNotIn("`Sniper`: diagnostic-only", combined)
        self.assertNotIn("`Taker`: diagnostic-only", combined)

        for path, text in texts.items():
            self.assertNotIn("no middle category", text, msg=f"stale no-middle-category language still present in {path}")

        bounded_tool_docs = [
            REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md",
            REPO_ROOT / "docs" / "OPEN_LIMITATIONS.md",
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md",
            REPO_ROOT / "docs" / "JIN_COMMAND_CARD_2026-04-27.md",
        ]
        for path in bounded_tool_docs:
            text = texts[path]
            self.assertIn("prelive_gate", text, msg=f"prelive gate bounded-tool language missing from {path}")
            self.assertIn("live_canary", text, msg=f"live_canary bounded-tool language missing from {path}")
            self.assertRegex(
                text,
                r"bounded (tools?|proving tools?)",
                msg=f"bounded-tool classification missing from {path}",
            )
            self.assertRegex(
                text,
                r"not\s+(the\s+)?final(\s+live)?\s+authorit",
                msg=f"final-authority boundary missing from {path}",
            )

        program_path = REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_QUALIFICATION_PACKET_PROGRAM_2026-05-05.md"
        program_text = texts[program_path]
        self.assertIn("prelive_gate", program_text)
        self.assertIn("live_canary", program_text)
        self.assertIn("final authority: observed truth under real conditions, not gate-green alone", program_text)

        generic_tuning_docs = [
            REPO_ROOT / "docs" / "BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md",
            REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md",
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_QUALIFICATION_PACKET_PROGRAM_2026-05-05.md",
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md",
            REPO_ROOT / "docs" / "JIN_COMMAND_CARD_2026-04-27.md",
        ]
        for path in generic_tuning_docs:
            self.assertIn("generic weapon tuning", texts[path], msg=f"generic weapon tuning boundary missing from {path}")

        limitation_phrases = json.loads((REPO_ROOT / "docs" / "DOCTRINE_LIMITATION_PHRASES.json").read_text(encoding="utf-8"))
        wallet_blocker_docs = [
            REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md",
            REPO_ROOT / "docs" / "OPEN_LIMITATIONS.md",
        ]
        for path in wallet_blocker_docs:
            text = texts[path]
            for key in (
                "canonical_live_nonce_truth_unavailable",
                "canonical_live_pending_wallet_tx_truth_unavailable",
                "strict_order_capable_live_fail_closed",
            ):
                self.assertIn(
                    limitation_phrases[key],
                    text,
                    msg=f"wallet live blocker phrase {key} missing from {path}",
                )

        sink_text = texts[REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md"]
        next_plan_text = texts[REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md"]
        self.assertIn("hookup truth", program_text)
        self.assertIn("order-capable live truth", program_text)
        self.assertIn("Contradiction Compression Pass", program_text)
        self.assertIn("Negative-Proof Pass", program_text)
        self.assertIn(
            "1. `Taker Live / Economic and Firing Trust Qualification`",
            program_text,
        )
        self.assertIn(
            "2. `Maker-Live / Economic Trust Qualification`",
            program_text,
        )
        self.assertIn(
            "Packet 1 `Taker Live / Economic and Firing Trust Qualification`",
            sink_text,
        )
        self.assertIn("split-brain", program_text)
        self.assertRegex(
            next_plan_text,
            r"first packet inside that lane is `Taker Live / Economic and Firing\s+Trust Qualification`",
        )
        self.assertRegex(program_text, r"smaller proofable\s+slices")
        self.assertRegex(
            program_text,
            r"one seam or one\s+tightly coupled authority set per slice",
        )
        self.assertIn("no weaker replacement of a stronger existing doctrine or validator surface", program_text)
        self.assertIn("Stronger existing doctrine or validator surfaces must not be replaced", sink_text)
        self.assertIn("contradiction compression pass", sink_text)
        self.assertIn("negative-proof pass", sink_text)
        self.assertIn("Current packet:", sink_text)
        self.assertIn("Packet 3 `Grip-Live / Wallet Live Trust Qualification`", sink_text)
        self.assertIn("Packet 2 is already active", program_text)
        self.assertIn("Packet 2 remains closeout-routed and no longer owns the active implementation body.", sink_text)
        self.assertIn("Packet 3 is now the active implementation body.", sink_text)
        self.assertNotIn("Packet 2 is next to open", program_text)
        self.assertNotIn("Next packet:\n- Packet 1 `Taker Live / Economic and Firing Trust Qualification`", sink_text)

        repo_dirty = bool(
            subprocess.check_output(
                ["git", "-C", str(REPO_ROOT), "status", "--short"],
                text=True,
            ).strip()
        )
        if repo_dirty:
            self.assertRegex(
                sink_text,
                r"current tree cleanliness: `dirty(?:[^`]*)`",
            )
            for path, text in texts.items():
                self.assertNotIn(
                    "working tree clean",
                    text,
                    msg=f"dirty repo is still being described as clean in {path}",
                )
        else:
            self.assertIn("current tree cleanliness: `clean`", sink_text)
            self.assertNotIn("working tree currently carries live-trust rehardening updates", combined)

    def test_live_trust_packet_1_taker_artifact_is_wired_and_complete(self):
        packet_path = (
            REPO_ROOT
            / "docs"
            / "BRO_PILOT_LIVE_TRUST_PACKET_1_TAKER_QUALIFICATION_2026-05-06.md"
        )
        packet_text = packet_path.read_text(encoding="utf-8")
        program_text = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_QUALIFICATION_PACKET_PROGRAM_2026-05-05.md"
        ).read_text(encoding="utf-8")
        sink_text = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md"
        ).read_text(encoding="utf-8")
        truth_text = (REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md").read_text(encoding="utf-8")
        next_plan_text = (REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md").read_text(encoding="utf-8")

        self.assertIn("Taker Live Trust Qualification", packet_text)
        self.assertIn("## Doctrine-Root Contradiction Verdict", packet_text)
        self.assertIn("## Stage/Config Dead-By-Construction Census", packet_text)
        self.assertIn("## Compensator-Fat / Scar-Tissue Census", packet_text)
        self.assertIn("## Sniper-Derived Taker Surface Map", packet_text)
        self.assertIn("## External Doctrine Proposal Audit: Taker Sword Doctrine Proposal", packet_text)
        self.assertIn("## Null-Hypothesis Subtree Removal Verdict", packet_text)
        self.assertIn("## Historical Closure Quarantine", packet_text)
        self.assertIn("## Bounded Implementation Ladder", packet_text)
        self.assertIn("BRO_HANDOFF_20260405T051017Z_SNIPER_TAKER_PACKET_CLOSEOUT.md", packet_text)

        packet_rel = "docs/BRO_PILOT_LIVE_TRUST_PACKET_1_TAKER_QUALIFICATION_2026-05-06.md"
        self.assertIn(packet_rel, program_text)
        self.assertIn(packet_rel, sink_text)
        self.assertIn(packet_rel, truth_text)
        self.assertIn(packet_rel, next_plan_text)

        self.assertIn("Current verdict:", packet_text)
        self.assertIn("- `bounded-live-test ready`", packet_text)
        self.assertIn("Packet 1 `Taker Live` now closes `bounded-live-test ready`", sink_text)
        self.assertIn("recovery/handoff owner classification is now explicitly keep-now dead power", sink_text)
        self.assertIn("keep-now explicit two-surface contract", packet_text)
        self.assertIn("current live late-window authority instead comes from the explicit runtime", packet_text)
        self.assertIn("top-level `taker.min_edge`", packet_text)
        self.assertIn("build_taker_competitiveness_policy(...)", packet_text)
        self.assertIn("TakerCompetitivenessConfig", packet_text)
        self.assertIn("TakerCompetitivenessEngine", packet_text)
        self.assertIn("prodesk/taker_competitiveness.py", packet_text)
        self.assertIn("removed sniper-family wrappers/events", packet_text)
        self.assertIn("quarantine as history only", packet_text)
        self.assertIn("fire-condition subtree under challenge:", sink_text)
        self.assertIn("helper/config/event compatibility mass is purge material after migration", sink_text)
        self.assertIn("`multi_oracle_edge_threshold_abs`", sink_text)
        self.assertIn("`min_visible_fill_ratio`", sink_text)
        self.assertIn("`final_window_enabled` / `final_window_sec`", sink_text)
        self.assertIn("`dynamic_preview_enabled`", sink_text)
        self.assertIn("EXTREME_ONLY_SELF_HARDENING_PACK_2026-05-08.md", sink_text)
        self.assertIn("canonical top-level `taker.min_edge=0.11`", sink_text)
        self.assertIn("raw `LINEAGE_ONLY_0_TO_20S` bucket is the current emitted late-window", sink_text)
        self.assertIn("no longer lets maker timing steal late", sink_text)
        self.assertIn("no longer lets legacy `execution_cutoff_sec` suppress", sink_text)
        self.assertIn("taker activation and `_run_taker()` now share one canonical stage-window", sink_text)
        self.assertIn("raw `LINEAGE_ONLY_0_TO_20S` lineage is not the live owner", sink_text)
        self.assertIn("Packet 1 removed taker-driven shared `OrderManager` soft-rate budget", sink_text)
        self.assertIn("maker-side `tracked_token_cleanup` / orphan cleanup still runs", sink_text)
        self.assertIn("one lifecycle contract for live authority", sink_text)
        self.assertIn("Historical Packet 1 closeout handoff:", packet_text)
        self.assertIn("Current pickup note:", packet_text)
        self.assertIn("historical closeout truth only", packet_text)
        self.assertNotIn("`taker.min_edge_by_stage.EXTREME_ONLY`", packet_text)
        self.assertNotIn("canonical paper `required_min_edge=0.11`", packet_text)

        self.assertNotIn("required_min_edge=0.18", packet_text)
        self.assertNotIn("legacy `sniper_taker_decision` remains", packet_text)
        self.assertNotIn("legacy bridge inputs `sniper.enabled` + `sniper.taker.enabled`", packet_text)
        self.assertNotIn("prodesk/sniper_tool.py", packet_text)
        self.assertNotIn("| Taker/Sniper Live |", sink_text)

    def test_live_trust_packet_2_maker_entry_artifact_and_recovery_root_death_prep_are_wired(self):
        packet_path = (
            REPO_ROOT
            / "docs"
            / "BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md"
        )
        packet_text = packet_path.read_text(encoding="utf-8")
        recovery_plan_text = (
            REPO_ROOT
            / "docs"
            / "BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_ROOT_DEATH_SURGERY_PLAN_2026-05-13.md"
        ).read_text(encoding="utf-8")
        program_text = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_QUALIFICATION_PACKET_PROGRAM_2026-05-05.md"
        ).read_text(encoding="utf-8")
        sink_text = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md"
        ).read_text(encoding="utf-8")
        truth_text = (REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md").read_text(encoding="utf-8")
        next_plan_text = (REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md").read_text(encoding="utf-8")
        limits_text = (REPO_ROOT / "docs" / "OPEN_LIMITATIONS.md").read_text(encoding="utf-8")
        diagnostic_tools_text = (REPO_ROOT / "docs" / "BRO_DIAGNOSTIC_TOOLS.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Maker Live Trust Qualification", packet_text)
        self.assertIn("## Packet-Local Lock Card", packet_text)
        self.assertIn("## Carry-Forward From Packet 1", packet_text)
        self.assertIn("## Open Investigation Lanes", packet_text)
        self.assertIn("## Immediate Packet 2 Pickup", packet_text)
        self.assertIn("## Pilot-Live Severity Covenant", packet_text)
        self.assertIn("## Critical Points That Must Be Defined", packet_text)
        self.assertIn("## Anti-Drift Reinforcement Layer", packet_text)
        self.assertIn("restore first, tune later", packet_text)
        self.assertIn("FMA", packet_text)
        self.assertIn("small recurring negative maker losses", packet_text)
        self.assertIn("SOLAR_SLUG_MAKER_CIRCUIT_SCHEMATIC.md", packet_text)
        self.assertIn("GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md", packet_text)
        self.assertIn("surviving-blocker runtime tribunal", packet_text)
        self.assertIn("support-shadow / probe family de-fat", packet_text)
        self.assertIn(
            "closeout routing + selector-family truth-sync + Packet 3 transition prep",
            packet_text,
        )
        self.assertIn("accessory competitiveness + sizing tribunals", packet_text)
        self.assertIn("diagnostic-surface hardening", packet_text)
        self.assertIn("Recurring self-hardening cadence", program_text)
        self.assertIn("## Pilot-Live Severity Lock", sink_text)
        self.assertIn("LT-008", sink_text)
        self.assertIn("LA-008", sink_text)
        self.assertIn("## Doctrine-Root Verdict", recovery_plan_text)
        self.assertIn("## Keep-Now Steel", recovery_plan_text)
        self.assertIn("## Kill Scope", recovery_plan_text)
        self.assertIn("## Replacement Owner Model", recovery_plan_text)
        self.assertIn("## Surgery Order", recovery_plan_text)
        self.assertIn("cancel-only fail-close", recovery_plan_text)
        self.assertIn("hold-to-settlement for real accepted exposure", recovery_plan_text)

        packet_rel = "docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md"
        recovery_plan_rel = (
            "docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_ROOT_DEATH_SURGERY_PLAN_2026-05-13.md"
        )
        recovery_extinction_rel = (
            "docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_HISTORY_COMPAT_EXTINCTION_PACKET_2026-05-14.md"
        )
        self.assertIn(packet_rel, program_text)
        self.assertIn(packet_rel, sink_text)
        self.assertIn(packet_rel, truth_text)
        self.assertIn(packet_rel, next_plan_text)
        self.assertIn(packet_rel, limits_text)
        self.assertIn(recovery_plan_rel, packet_text)
        self.assertIn(recovery_plan_rel, program_text)
        self.assertIn(recovery_plan_rel, sink_text)
        self.assertIn(recovery_plan_rel, truth_text)
        self.assertIn(recovery_plan_rel, next_plan_text)
        self.assertIn(recovery_plan_rel, limits_text)
        self.assertIn(recovery_extinction_rel, recovery_plan_text)
        self.assertIn(recovery_extinction_rel, sink_text)
        self.assertIn(recovery_extinction_rel, truth_text)
        self.assertIn(recovery_extinction_rel, next_plan_text)
        self.assertIn(recovery_extinction_rel, limits_text)
        self.assertIn("current packet = `Packet 2 Maker-Live / Economic Trust Qualification`", packet_text)
        self.assertIn(
            "current mode =\n  `closeout routing + selector-family truth-sync + Packet 3 transition prep`",
            packet_text,
        )
        self.assertIn("current first work = top truth / pickup / board routing sync", packet_text)
        self.assertIn("current second work = stale Packet 2 residue extinction", packet_text)
        self.assertIn("current third work = read-only Packet 3 wallet owner audit", packet_text)
        self.assertIn("cancel-only fail-close for open", packet_text)
        self.assertIn("`docs/PROJECT_TRUTH_STATE.md` as the broad repo truth screen", packet_text)
        self.assertIn("maker gate opens at `15s`", sink_text)
        self.assertIn("taker handoff opens at `7s`", sink_text)
        self.assertIn("effective maker new-risk submit window remains `(7.0, 15.0]`", sink_text)
        self.assertIn("optional later archaeology packet", sink_text)
        self.assertIn("compatibility archaeology + ignored dead-key support", sink_text)
        self.assertIn("current watched proof does **not** prove the self-heal caused those", next_plan_text)
        self.assertIn(
            "the older selector-owned low-edge one-sided reject family is retired from",
            next_plan_text,
        )
        self.assertIn("canonical maker selection authority", next_plan_text)
        self.assertIn("normal one-sided prunes", diagnostic_tools_text)
        self.assertIn("not real blocker-family authority", diagnostic_tools_text)
        self.assertIn("recovery / unwind current-owner cut is materially landed", program_text)
        self.assertIn("compatibility readers, ignored dead-key support, and", program_text)
        self.assertIn("shared recovery / unwind spine = cut from the current", sink_text)

    def test_jin_relock_pack_is_hardcore_controller_and_bridges_route_through_it(self):
        relock_text = (REPO_ROOT / "docs" / "JIN_RELOCK_PACK_2026-05-12.md").read_text(
            encoding="utf-8"
        )
        recovery_text = (REPO_ROOT / "docs" / "JIN_THREAD_RECOVERY_RUNBOOK.md").read_text(
            encoding="utf-8"
        )
        sink_text = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md"
        ).read_text(encoding="utf-8")
        packet_text = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md"
        ).read_text(encoding="utf-8")
        program_text = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_QUALIFICATION_PACKET_PROGRAM_2026-05-05.md"
        ).read_text(encoding="utf-8")

        self.assertIn("pickup bridge", relock_text)
        self.assertIn("relock controller", relock_text)
        self.assertIn("anti-drift gate", relock_text)
        self.assertIn("not owner-law", relock_text)
        self.assertIn("not current packet truth by itself", relock_text)
        self.assertIn("## Relock Modes", relock_text)
        self.assertIn("## Internalization Proof", relock_text)
        self.assertIn("6 lines", relock_text)
        self.assertIn("## Lock Scorecard", relock_text)
        self.assertIn("overall average is `>=95`", relock_text)
        self.assertIn("## Authority Ladder", relock_text)
        self.assertIn("`constitutional / law`", relock_text)
        self.assertIn("`active pickup truth`", relock_text)
        self.assertIn("`runtime owners`", relock_text)
        self.assertIn("`support-only`", relock_text)
        self.assertIn("`historical-only / quarantine`", relock_text)
        self.assertIn("## Drift Attack Routine", relock_text)
        self.assertIn("stale-owner scan", relock_text)
        self.assertIn("semantic-split scan", relock_text)
        self.assertIn("## Forced Rehardening Cadence", relock_text)
        self.assertIn("after every 2 investigation sections", relock_text)
        self.assertIn("Current maker surgery overlay", relock_text)
        self.assertIn("support docs and schematic docs are never enough by themselves", relock_text)

        relock_rel = "docs/JIN_RELOCK_PACK_2026-05-12.md"
        self.assertIn(relock_rel, recovery_text)
        self.assertIn("BRO-wide hardcore relock controller", recovery_text)
        self.assertIn(relock_rel, sink_text)
        self.assertIn("BRO-wide anti-drift relock front door", sink_text)
        self.assertIn(relock_rel, packet_text)
        self.assertIn("anti-drift front door", packet_text)
        self.assertIn(relock_rel, program_text)
        self.assertIn("BRO-wide anti-drift relock controller", program_text)

    def test_bro_canonical_doctrine_matches_current_taker_stage_authority(self):
        doctrine_text = (REPO_ROOT / "BRO_CANONICAL_DOCTRINE.txt").read_text(encoding="utf-8")

        self.assertIn("`SNIPER_PRIMARY` is diagnostic-only in canonical live doctrine", doctrine_text)
        self.assertIn(
            "lineage-stage-local final-window overrides are reserved for explicit diagnostic or",
            doctrine_text,
        )
        self.assertIn("`taker_decision` with conviction", doctrine_text)
        self.assertIn(
            "Canonical live taker authority is not active in this lineage stage.",
            doctrine_text,
        )
        self.assertNotIn("SNIPER_PRIMARY is taker-only", doctrine_text)
        self.assertNotIn("SNIPER_PRIMARY must be taker-only", doctrine_text)
        self.assertNotIn(
            "SNIPER_PRIMARY may use a stricter stage-local final window than other taker stages",
            doctrine_text,
        )
        self.assertNotIn(
            "legacy `sniper_taker_decision` remains a compatibility alias",
            doctrine_text,
        )
        self.assertNotIn(
            "legacy `taker_decision` remains a compatibility alias",
            doctrine_text,
        )

    def test_doctrine_runbook_spells_out_maker_taker_market_reference_asymmetry(self):
        runbook_text = (REPO_ROOT / "docs" / "DOCTRINE_RUNBOOK.md").read_text(encoding="utf-8")

        self.assertIn("maker market-reference uses:", runbook_text)
        self.assertIn("backfilled_paired_touch", runbook_text)
        self.assertIn("taker market-reference uses:", runbook_text)
        self.assertIn(
            "midpoint-backed `direct_midpoint` only when ws midpoint exists",
            runbook_text,
        )
        self.assertIn(
            "otherwise maker fails closed with missing / non-authoritative market reference truth",
            runbook_text,
        )
        self.assertIn(
            "fully missing ws market reference remains explicit fail-closed `market_probability_missing`",
            runbook_text,
        )

    def test_owned_market_authority_split_is_explicit_in_current_doctrine_surfaces(self):
        runbook_text = (REPO_ROOT / "docs" / "DOCTRINE_RUNBOOK.md").read_text(encoding="utf-8")
        validation_text = (REPO_ROOT / "docs" / "CANONICAL_VALIDATION_PATH.md").read_text(encoding="utf-8")
        doctrine_text = (REPO_ROOT / "BRO_CANONICAL_DOCTRINE.txt").read_text(encoding="utf-8")

        self.assertIn("`owned_market_pair`", runbook_text)
        self.assertIn("`challenger_market_pair`", runbook_text)
        self.assertIn("`lifecycle_watch_tokens`", runbook_text)
        self.assertIn("implementation residue, not canonical doctrine", runbook_text)
        self.assertIn("pending/prewarm candidates may be transport-watched for warm-up", validation_text)
        self.assertIn("may contribute active pair-truth failure", validation_text)
        self.assertIn("one authoritative target", doctrine_text)
        self.assertIn("pending/prewarm candidates may exist for controlled warm-up", doctrine_text)
        self.assertIn("lifecycle watch truth must not impersonate active target-pair truth", doctrine_text)

    def test_doctrine_runbook_demotes_stage_family_to_compatibility_only(self):
        runbook_text = (REPO_ROOT / "docs" / "DOCTRINE_RUNBOOK.md").read_text(encoding="utf-8")
        edge_text = (REPO_ROOT / "docs" / "EDGE_TRUTH_RUNBOOK.md").read_text(encoding="utf-8")

        self.assertIn("## Historical Lineage Boundary", runbook_text)
        self.assertIn("lineage-stage ancestry may still appear", runbook_text)
        self.assertIn("it is never a second live", runbook_text)
        self.assertIn("owned-market continuity", runbook_text)
        self.assertIn("lane-local maker/taker gates", runbook_text)
        self.assertIn("`open_order_cleanup_required`", runbook_text)
        self.assertIn("`settlement_hold_required`", runbook_text)
        self.assertIn("`open_order_cleanup_required` (bool)", edge_text)
        self.assertIn("`settlement_hold_required` (bool)", edge_text)
        self.assertNotIn("## Stage-Family Compatibility Note", runbook_text)
        self.assertNotIn("`reduce_only_recovery_allowed` (bool", edge_text)
        self.assertNotIn("`preexpiry_emergency_taker_allowed` (bool)", edge_text)
        self.assertNotIn("legacy `sniper_taker_decision` remains a compatibility alias", runbook_text)
        self.assertNotIn("legacy `taker_decision` remains a compatibility alias", runbook_text)

    def test_recovery_root_death_docs_do_not_teach_live_reduce_only_recovery_surfaces(self):
        current_baseline = (REPO_ROOT / "docs" / "CURRENT_BASELINE.md").read_text(encoding="utf-8")
        baseline_sync = (REPO_ROOT / "docs" / "BASELINE_DOC_SYNC_STATUS.md").read_text(encoding="utf-8")
        gate_board = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_GATE_LEGITIMACY_BOARD_2026-05-10.md"
        ).read_text(encoding="utf-8")
        scar_board = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_SMALL_LOSS_SCAR_TISSUE_BOARD_2026-05-10.md"
        ).read_text(encoding="utf-8")
        recovery_plan = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_ROOT_DEATH_SURGERY_PLAN_2026-05-13.md"
        ).read_text(encoding="utf-8")
        project_truth = (REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md").read_text(encoding="utf-8")
        next_packet = (REPO_ROOT / "docs" / "NEXT_PACKET_PLAN.md").read_text(encoding="utf-8")

        self.assertNotIn("Current-code nightly report now surfaces `reduce_only_recovery` diagnostics", current_baseline)
        self.assertNotIn("emits `reduce_only_recovery` diagnostics", baseline_sync)
        self.assertNotIn("shared recovery safety spine", gate_board)
        self.assertNotIn("KEEP BUT FENCE", gate_board.splitlines()[21])
        self.assertIn("CUT / HISTORICAL-COMPAT LINEAGE", gate_board)
        self.assertIn("CUT / HISTORICAL-COMPAT LINEAGE", scar_board)
        self.assertIn("open_order_cleanup_required", recovery_plan)
        self.assertIn("unresolved_lifecycle_obligation", recovery_plan)
        self.assertNotIn("cancel_cleanup_required", recovery_plan)
        self.assertNotIn("unresolved_lifecycle_residue", recovery_plan)
        self.assertNotIn("next packet to open inside that lane is `Maker-Live / Economic Trust Qualification`", project_truth)
        self.assertNotIn("recovery / unwind` compatibility archaeology + ignored dead-key support closeout", next_packet)


if __name__ == "__main__":
    unittest.main()
