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


class OperatorDocsCanonicalTests(unittest.TestCase):
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
            "python observer.py --config configs/profiles/paper_universal.yaml",
            "python scripts/config_consistency_audit.py --primary configs/profiles/paper_universal.yaml --secondary execution_config.yaml",
        ]
        forbidden = [
            "python scripts/websocket_hardening_audit.py --config execution_config.yaml",
            "python scripts/time_discipline_audit.py --config execution_config.yaml",
            "python scripts/alert_profile_audit.py --config execution_config.yaml",
            "python scripts/forensics_bundle.py --log-dir ./logs_exec/paper_universal --run-id <run_id> --config execution_config.yaml --out-dir ./exports",
            "python observer.py --config config.yaml",
            "python scripts/config_consistency_audit.py --primary execution_config.yaml --secondary config.yaml",
        ]
        for snippet in required:
            self.assertIn(snippet, text)
        for snippet in forbidden:
            self.assertNotIn(snippet, text)

    def test_project_truth_state_is_repo_truth_screen(self):
        text = (REPO_ROOT / "docs" / "PROJECT_TRUTH_STATE.md").read_text(encoding="utf-8")
        self.assertIn("repo-level current truth screen", text)

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
                "docs/GALAXY_MEGA_MAKER_CANNON_BLUEPRINT_2026-04-28.md",
                text,
                msg=f"maker doctrine anchor missing from {path}",
            )
            self.assertIn("15-20s", text, msg=f"late-window maker doctrine missing from {path}")
            self.assertIn("10-15s", text, msg=f"maker sweet-spot doctrine missing from {path}")
            for token in forbidden_tokens:
                self.assertNotIn(token, text, msg=f"drift-era maker doctrine phrasing still present in {path}")

    def test_doctrine_authority_docs_name_galaxy_blueprint_as_intended_maker_anchor(self):
        targets = [
            REPO_ROOT / "BRO_TEXT_GUIDE.txt",
            REPO_ROOT / "BRO_EDGE_DOCTRINE.txt",
            REPO_ROOT / "docs" / "DOCTRINE_RUNBOOK.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "docs/GALAXY_MEGA_MAKER_CANNON_BLUEPRINT_2026-04-28.md",
                text,
                msg=f"maker blueprint missing from doctrine authority doc {path}",
            )
            self.assertIn("15-20s", text, msg=f"intended maker doctrine window missing from {path}")
            self.assertIn("10-15s", text, msg=f"maker sweet-spot doctrine missing from {path}")

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
        self.assertIn("current BRO fighter-specific runtime/timing/stage authority lives in", text)
        self.assertIn("`MAKER_TAKER_SELECTIVE`: maker yes, taker no", text)
        self.assertIn("`SNIPER_PRIMARY`: maker no, taker no", text)
        self.assertIn("`EXTREME_ONLY`: maker no, taker no", text)

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

        repo_dirty = bool(
            subprocess.check_output(
                ["git", "-C", str(REPO_ROOT), "status", "--short"],
                text=True,
            ).strip()
        )
        if repo_dirty:
            self.assertIn("current tree cleanliness: `dirty`", sink_text)
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
        self.assertIn("## External Blueprint Audit: Taker Sword Blueprint", packet_text)
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
        self.assertIn("`effective_stage` is the live authority / action surface", packet_text)
        self.assertIn("`stage_bucket` is the raw timing-lineage / diagnostic surface", packet_text)
        self.assertIn("CANONICAL_EDGE_STAGE_POLICY", packet_text)
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
        self.assertIn("raw `EXTREME_ONLY` bucket still exists, but it no longer owns", sink_text)
        self.assertIn("no longer lets maker timing steal late `EXTREME_ONLY`", sink_text)
        self.assertIn("no longer lets legacy `execution_cutoff_sec` suppress", sink_text)
        self.assertIn("taker activation and `_run_taker()` now share one canonical stage-window", sink_text)
        self.assertIn("raw `EXTREME_ONLY` stage policy is no longer the live owner", sink_text)
        self.assertIn("Packet 1 removed taker-driven shared `OrderManager` soft-rate budget", sink_text)
        self.assertIn("maker-side `tracked_token_cleanup` / orphan cleanup still runs", sink_text)
        self.assertIn("keep-now explicit two-surface contract", sink_text)
        self.assertNotIn("`taker.min_edge_by_stage.EXTREME_ONLY`", packet_text)
        self.assertNotIn("canonical paper `required_min_edge=0.11`", packet_text)

        self.assertNotIn("required_min_edge=0.18", packet_text)
        self.assertNotIn("legacy `sniper_taker_decision` remains", packet_text)
        self.assertNotIn("legacy bridge inputs `sniper.enabled` + `sniper.taker.enabled`", packet_text)
        self.assertNotIn("prodesk/sniper_tool.py", packet_text)
        self.assertNotIn("| Taker/Sniper Live |", sink_text)

    def test_live_trust_packet_2_maker_entry_artifact_is_wired_and_recovery_ready(self):
        packet_path = (
            REPO_ROOT
            / "docs"
            / "BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md"
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
        limits_text = (REPO_ROOT / "docs" / "OPEN_LIMITATIONS.md").read_text(encoding="utf-8")

        self.assertIn("Maker Live Trust Qualification", packet_text)
        self.assertIn("## Packet-Local Lock Card", packet_text)
        self.assertIn("## Carry-Forward From Packet 1", packet_text)
        self.assertIn("## Open Investigation Lanes", packet_text)
        self.assertIn("## Immediate Recovery Pickup", packet_text)
        self.assertIn("restore first, tune later", packet_text)
        self.assertIn("FMA", packet_text)
        self.assertIn("small recurring negative maker losses", packet_text)
        self.assertIn("SOLAR_SLUG_MAKER_CIRCUIT_SCHEMATIC.md", packet_text)
        self.assertIn("GALAXY_MEGA_MAKER_CANNON_BLUEPRINT_2026-04-28.md", packet_text)

        packet_rel = "docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md"
        self.assertIn(packet_rel, program_text)
        self.assertIn(packet_rel, sink_text)
        self.assertIn(packet_rel, truth_text)
        self.assertIn(packet_rel, next_plan_text)
        self.assertIn(packet_rel, limits_text)

    def test_bro_canonical_doctrine_matches_current_taker_stage_authority(self):
        doctrine_text = (REPO_ROOT / "BRO_CANONICAL_DOCTRINE.txt").read_text(encoding="utf-8")

        self.assertIn("SNIPER_PRIMARY is diagnostic-only in canonical live doctrine", doctrine_text)
        self.assertIn(
            "stage-local final-window overrides are reserved for explicit diagnostic or",
            doctrine_text,
        )
        self.assertIn("`taker_decision` with conviction", doctrine_text)
        self.assertIn(
            "Canonical live taker authority is not active in this stage.",
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
        self.assertIn("bounded_single_side_touch", runbook_text)
        self.assertIn("taker market-reference uses:", runbook_text)
        self.assertIn(
            "`bounded_single_side_touch` when midpoint is unavailable and exactly one ws side is present",
            runbook_text,
        )
        self.assertIn(
            "fully missing ws market reference remains explicit fail-closed `market_probability_missing`",
            runbook_text,
        )

    def test_doctrine_runbook_names_effective_stage_and_stage_bucket_as_stage_signals(self):
        runbook_text = (REPO_ROOT / "docs" / "DOCTRINE_RUNBOOK.md").read_text(encoding="utf-8")

        self.assertIn("`effective_stage` and `stage_bucket`", runbook_text)
        self.assertIn("legacy `stage` and `raw_stage` as compatibility aliases only", runbook_text)
        self.assertIn("`taker_decision` event", runbook_text)
        self.assertIn("`extreme_only_on_arrival`", runbook_text)
        self.assertNotIn("legacy `sniper_taker_decision` remains a compatibility alias", runbook_text)
        self.assertNotIn("legacy `taker_decision` remains a compatibility alias", runbook_text)
        self.assertNotIn("`sniper_primary_on_arrival`", runbook_text)

    def test_doctrine_and_packet_name_timing_gate_handoff_override_recovery_path(self):
        runbook_text = (REPO_ROOT / "docs" / "DOCTRINE_RUNBOOK.md").read_text(encoding="utf-8")
        packet_text = (
            REPO_ROOT / "docs" / "BRO_PILOT_LIVE_TRUST_PACKET_1_TAKER_QUALIFICATION_2026-05-06.md"
        ).read_text(encoding="utf-8")

        self.assertIn("timing-gate handoff override path", runbook_text)
        self.assertIn("does not restore normal taker live authority", runbook_text)
        self.assertIn("timing_gate_handoff_override_active=true", packet_text)
        self.assertIn("outside the nominal `<=7s` emergency window", packet_text)
        self.assertIn("`maker_to_taker_recovery_handoff_disabled`", packet_text)


if __name__ == "__main__":
    unittest.main()
