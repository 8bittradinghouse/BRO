import pathlib
import re
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
        self.assertIn("code_fingerprint_sha256", text)
        self.assertIn("Promotion-grade evidence must be manifest-backed", text)
        self.assertIn("`profile_name`", text)

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
        self.assertIn("`EXTREME_ONLY`: maker no, taker yes", text)

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


if __name__ == "__main__":
    unittest.main()
