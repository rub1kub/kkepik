"""Isolated, stdlib-only safety checks; never contacts Telegram or production DB."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("repair_20261013", Path(__file__).resolve().parents[1] / "scripts/repair_schedule_20261013.py")
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


def sample_row(**changes):
    row = dict(id=1, platform="telegram", direction="outgoing", chat_id="123", message_id="456",
               schedule_date=repair.DATE, schedule_type="groups", message_kind="schedule",
               telegram_method="SendPhoto", entity="116-КД9-3ССА", content_preview="Old caption",
               content_hash=repair.digest("Old caption"), updated_at="2026-10-12 11:40:00")
    row.update(changes)
    return row


def snapshot():
    return {"user": {"role": repair.STUDENT, "name_or_group": "116-КД9-3ССА", "is_class_teacher": 0, "class_group": None},
            "tracked": [["116-КД9-3ССА", 1]], "preferences": None}


def target():
    return {"row": sample_row(), "snapshot": snapshot(), "method": "editMessageMedia",
            "html": "<b>New caption</b>", "plain": "New caption", "new_hash": repair.digest("New caption"),
            "new_kind": "schedule", "image": {"file": "a" * 16 + ".png", "sha": repair.digest(b"image")}}


class SafetyTests(unittest.TestCase):
    def mocked_apply(self, api_results, *, previous=None, guard=None):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        audit = Path(folder)
        (audit / ("a" * 16 + ".png")).write_bytes(b"image")
        self.enterContext(patch.object(repair, "AUDIT", audit))
        self.enterContext(patch.object(repair, "check_source"))
        self.enterContext(patch.object(repair, "token_from_env", return_value="fake-token"))
        self.enterContext(patch.object(repair, "previous_events", return_value=previous or {}))
        self.enterContext(patch.object(repair.time, "sleep"))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        api = self.enterContext(patch.object(repair, "api_call", side_effect=api_results))
        guarded = self.enterContext(patch.object(repair, "guard_target", side_effect=guard, return_value=None))
        event = self.enterContext(patch.object(repair, "durable_event"))
        registry = self.enterContext(patch.object(repair, "update_registry"))
        return api, guarded, event, registry

    def test_scope_requires_exact_date_kind_source_and_private_chat(self):
        repair.validate_row(sample_row())
        for fields in ({"schedule_date": "14.10.2026"}, {"platform": "max"}, {"direction": "incoming"},
                       {"chat_id": "-123"}, {"message_kind": "forwarded_post"}, {"telegram_method": "SendDocument"},
                       {"content_hash": repair.digest("different")}, {"content_preview": None}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                repair.validate_row(sample_row(**fields))

    def test_no_fresh_send_methods_possible(self):
        for method in ("sendMessage", "sendPhoto", "forwardMessage", "deleteMessage", "editMessageCaption"):
            with self.subTest(method=method), self.assertRaises(ValueError), patch.object(repair.urllib.request, "urlopen") as http:
                repair.api_call("not-a-real-token", method, {})
            http.assert_not_called()

    def test_recipient_group_guard_uses_actual_tracking(self):
        self.assertTrue(repair.allowed_entity(sample_row(), snapshot()))
        changed = snapshot()
        changed["tracked"] = [["103-Д9-3ИНС", 1]]
        self.assertFalse(repair.allowed_entity(sample_row(), changed))
        self.assertFalse(repair.allowed_entity(sample_row(), None))

    def test_teacher_entity_must_match_current_name(self):
        snap = snapshot()
        snap["user"].update(role=repair.TEACHER, name_or_group="Учитель И.И.")
        self.assertTrue(repair.allowed_entity(sample_row(entity="Учитель И.И."), snap))
        self.assertFalse(repair.allowed_entity(sample_row(entity="Другой А.А."), snap))

    def test_inplace_repair_does_not_depend_on_notification_preferences(self):
        snap = snapshot()
        snap["preferences"] = {"preferences": '{"notifications_enabled":false}', "updated_at": "2026-10-12"}
        self.assertTrue(repair.allowed_entity(sample_row(), snap))

    def test_changed_recipient_and_registry_fail_closed(self):
        item = target()
        with patch.object(repair, "user_snapshot", return_value=None):
            self.assertEqual(repair.guard_target(item), "recipient_changed")
        for changed in (None, sample_row(content_hash="foreign-change"), sample_row(entity="different")):
            with patch.object(repair, "user_snapshot", return_value=snapshot()), patch.object(repair, "current_registry_row", return_value=changed):
                self.assertEqual(repair.guard_target(item), "registry_changed")

    def test_unchanged_caption_does_not_prove_photo_fixed(self):
        item = target()
        item.update(html="Old caption", plain="Old caption", new_hash=repair.digest("Old caption"))
        row = sample_row(updated_at="2026-10-12 20:00:00")
        with patch.object(repair, "user_snapshot", return_value=snapshot()), patch.object(repair, "current_registry_row", return_value=row):
            self.assertIsNone(repair.guard_target(item))

    def test_plan_rejects_duplicate_or_traversing_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            audit = Path(folder)
            (audit / ("a" * 16 + ".png")).write_bytes(b"image")
            plan = {"date": repair.DATE, "source_sha": repair.SOURCE_SHA, "version": 1,
                    "targets": [target()], "skipped": {}}
            path = audit / "plan.json"
            with patch.object(repair, "AUDIT", audit), patch.object(repair, "PLAN", path):
                path.write_text(json.dumps(plan))
                self.assertEqual(len(repair.load_plan()["targets"]), 1)
                plan["targets"].append(target())
                path.write_text(json.dumps(plan))
                with self.assertRaises(ValueError):
                    repair.load_plan()
                plan["targets"] = [target()]
                plan["targets"][0]["image"]["file"] = "../../.env"
                path.write_text(json.dumps(plan))
                with self.assertRaises(ValueError):
                    repair.load_plan()

    def test_default_dryrun_has_no_network_or_writes(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(repair, "PLAN", Path(folder) / "absent.json"), \
                patch.object(repair, "candidate_rows", return_value=[sample_row()]), \
                patch.object(repair, "api_call") as http, patch.object(repair, "durable_file") as write, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(repair.main([]), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result["no_network_or_writes"])
            http.assert_not_called()
            write.assert_not_called()

    def test_missing_warning_is_a_valid_existing_text_target(self):
        row = sample_row(message_kind="schedule_missing_group", telegram_method="SendMessage", entity="147-Д9-1БТС")
        repair.validate_row(row)
        snap = snapshot()
        snap["tracked"] = [["147-Д9-1БТС", 1]]
        self.assertTrue(repair.allowed_entity(row, snap))

    def test_plaintext_and_telegram_utf16_limits(self):
        self.assertEqual(repair.plain('<b>A &amp; B</b> 🧘'), "A & B 🧘")
        self.assertEqual(repair.visible_length("<b>🧘</b>"), 2)

    def test_apply_requires_prepared_plan_and_does_not_call_api(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(repair, "PLAN", Path(folder) / "absent.json"), \
                patch.object(repair, "api_call") as http, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(repair.main(["--apply"]), 1)
            http.assert_not_called()

    def test_source_hash_cannot_be_silently_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            source.write_bytes(b"different-pdf")
            with patch.object(repair, "SOURCE", source), self.assertRaises(ValueError):
                repair.check_source()

    def test_apply_edits_exact_existing_message_then_updates_registry(self):
        response = {"message_id": 456, "chat": {"id": 123}, "caption": "New caption"}
        api, _, event, registry = self.mocked_apply([{"username": "kkepik_bot", "is_bot": True}, response])
        item = target()
        repair.apply({"targets": [item], "skipped": {}})
        self.assertEqual([call.args[1] for call in api.call_args_list], ["getMe", "editMessageMedia"])
        values = api.call_args_list[1].args[2]
        self.assertEqual((values["chat_id"], values["message_id"]), (123, 456))
        self.assertEqual([call.args[0]["status"] for call in event.call_args_list], ["attempting", "edited"])
        registry.assert_called_once_with(item)

    def test_rate_limit_rechecks_subscription_before_retry(self):
        api, _, event, registry = self.mocked_apply(
            [{"username": "kkepik_bot", "is_bot": True}, repair.ApiFailure(429, retry_after=1)],
            guard=[None, None, "recipient_changed"])
        repair.apply({"targets": [target()], "skipped": {}})
        self.assertEqual(api.call_count, 2)
        self.assertEqual(event.call_args.args[0]["status"], "recipient_changed")
        registry.assert_not_called()

    def test_ambiguous_network_error_retries_same_idempotent_edit_only(self):
        response = {"message_id": 456, "chat": {"id": 123}, "caption": "New caption"}
        api, _, _, registry = self.mocked_apply([
            {"username": "kkepik_bot", "is_bot": True}, repair.ApiFailure(0), response])
        repair.apply({"targets": [target()], "skipped": {}})
        self.assertEqual([call.args[1] for call in api.call_args_list], ["getMe", "editMessageMedia", "editMessageMedia"])
        self.assertEqual(api.call_args_list[1].args[2], api.call_args_list[2].args[2])
        registry.assert_called_once()

    def test_successful_resume_reconciles_registry_without_another_edit(self):
        api, _, event, registry = self.mocked_apply(
            [{"username": "kkepik_bot", "is_bot": True}], previous={1: {"status": "edited"}})
        repair.apply({"targets": [target()], "skipped": {}})
        self.assertEqual([call.args[1] for call in api.call_args_list], ["getMe"])
        registry.assert_called_once()
        event.assert_not_called()

    def test_deleted_message_never_falls_back_to_send(self):
        api, _, event, registry = self.mocked_apply([
            {"username": "kkepik_bot", "is_bot": True}, repair.ApiFailure(400, "Bad Request: message to edit not found")])
        repair.apply({"targets": [target()], "skipped": {}})
        self.assertEqual(api.call_count, 2)
        self.assertEqual(event.call_args.args[0]["status"], "unavailable")
        registry.assert_not_called()

    def test_wrong_bot_and_unexpected_response_halt_before_registry_mutation(self):
        api, _, _, registry = self.mocked_apply([{"username": "other_bot", "is_bot": True}])
        with self.assertRaises(ValueError):
            repair.apply({"targets": [target()], "skipped": {}})
        self.assertEqual(api.call_count, 1)
        registry.assert_not_called()

    def test_message_not_modified_is_successful_idempotent_recovery(self):
        _, _, event, registry = self.mocked_apply([
            {"username": "kkepik_bot", "is_bot": True}, repair.ApiFailure(400, "Bad Request: message is not modified")])
        repair.apply({"targets": [target()], "skipped": {}})
        self.assertEqual(event.call_args.args[0]["status"], "not_modified")
        registry.assert_called_once()

    def test_completion_report_is_false_for_every_unfixed_guard(self):
        payload = {"targets": [target()], "skipped": {}}
        self.assertTrue(repair.completion_report(payload, repair.Counter(edited=1), True)["complete"])
        for failed in ("recipient_changed", "registry_changed", "retry_exhausted", "unavailable"):
            with self.subTest(failed=failed):
                report = repair.completion_report(payload, repair.Counter({failed: 1}), True)
                self.assertFalse(report["complete"])
                self.assertEqual(report["guard_report"][failed], 1)
        self.assertFalse(repair.completion_report(payload, repair.Counter(), False)["complete"])

    def test_durable_summary_contains_explicit_zero_guard_counts(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(repair, "AUDIT", Path(folder)):
            report = repair.completion_report({"targets": [target()], "skipped": {}}, repair.Counter(edited=1), True)
            repair.persist_report(report)
            path = Path(folder) / "summary.json"
            saved = json.loads(path.read_text())
            self.assertTrue(saved["complete"])
            self.assertEqual(saved["guard_report"], {"recipient_changed": 0, "registry_changed": 0, "retry_exhausted": 0, "unavailable": 0})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
