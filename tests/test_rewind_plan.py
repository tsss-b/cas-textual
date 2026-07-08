import pytest

from castcode.records import AssistantRecord, NoticeRecord, ToolRecord, UserRecord
from castcode.rewind import (
    apply_rewind_uuid_map,
    build_branch_plan,
    plan_rewind,
    remap_checkpoints,
    remap_records,
    remap_uuid,
)


def _records():
    return [
        UserRecord("first", "u1"),
        AssistantRecord("a1"),
        UserRecord("second", "u2"),
        ToolRecord("Read", "Read", state="done"),
        AssistantRecord("a2"),
        NoticeRecord("note"),
    ]


def _checkpoints():
    return [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {"turn_index": 1, "user_uuid": "u2", "keep_uuid": "a1"},
    ]


def test_restore_drops_selected_user_row_and_checkpoint():
    plan = plan_rewind(_records(), _checkpoints(), _checkpoints()[1], "restore")

    assert plan.fork_uuid == "a1"
    assert plan.fresh_session is False
    assert plan.records_before_remap == [UserRecord("first", "u1"), AssistantRecord("a1")]
    assert plan.checkpoints_before_remap == [_checkpoints()[0]]
    assert plan.last_assistant_uuid == "a1"
    assert plan.edit_text is None


def test_first_turn_restore_is_fresh_session_and_no_fork_target():
    plan = plan_rewind(_records(), _checkpoints(), _checkpoints()[0], "restore")

    assert plan.fork_uuid is None
    assert plan.fresh_session is True
    assert plan.records_before_remap == []
    assert plan.checkpoints_before_remap == []
    assert plan.last_assistant_uuid is None


def test_operation_to_fork_target_mapping_lives_in_plan_rewind():
    restore = plan_rewind(_records(), _checkpoints(), _checkpoints()[1], "restore")
    reload = plan_rewind(_records(), _checkpoints(), _checkpoints()[1], "reload")

    assert restore.fork_uuid == "a1"
    assert reload.fork_uuid == "a1"


def test_reload_returns_original_edit_text():
    plan = plan_rewind(_records(), _checkpoints(), _checkpoints()[1], "reload")

    assert plan.edit_text == "second"


def test_reload_accepts_explicit_edit_text():
    plan = plan_rewind(
        _records(),
        _checkpoints(),
        _checkpoints()[1],
        "reload",
        edit_text="edited",
    )

    assert plan.edit_text == "edited"


def test_checkpoint_truncation_has_no_orphan_user_uuid():
    plan = plan_rewind(_records(), _checkpoints(), _checkpoints()[1], "restore")
    user_uuids = {record.uuid for record in plan.records_before_remap if isinstance(record, UserRecord)}

    assert {checkpoint["user_uuid"] for checkpoint in plan.checkpoints_before_remap} <= user_uuids


def test_stale_or_mismatched_turn_index_raises():
    checkpoint = {"turn_index": 0, "user_uuid": "u2", "keep_uuid": "a1"}

    with pytest.raises(ValueError, match="turn_index does not match"):
        plan_rewind(_records(), _checkpoints(), checkpoint, "restore")


def test_out_of_range_turn_index_raises():
    checkpoint = {"turn_index": 99, "user_uuid": "u2", "keep_uuid": "a1"}

    with pytest.raises(ValueError, match="out of range"):
        plan_rewind(_records(), _checkpoints(), checkpoint, "restore")


def test_uuid_remap_applies_to_records_checkpoints_and_last_assistant():
    plan = plan_rewind(_records(), _checkpoints(), _checkpoints()[1], "restore")
    applied = apply_rewind_uuid_map(plan, {"u1": "u1b", "a1": "a1b"})

    assert applied.records == [UserRecord("first", "u1b"), AssistantRecord("a1")]
    assert applied.checkpoints == [
        {"turn_index": 0, "user_uuid": "u1b", "keep_uuid": None}
    ]
    assert applied.last_assistant_uuid == "a1b"


def test_missing_selected_user_uuid_surfaces():
    records = [UserRecord("first", "u1")]
    checkpoints = [{"turn_index": 0, "user_uuid": "missing", "keep_uuid": None}]

    with pytest.raises(ValueError, match="selected user record not found"):
        plan_rewind(records, checkpoints, checkpoints[0], "restore")


def test_missing_checkpoint_user_uuid_raises_before_matching_none_uuid_record():
    records = [ToolRecord("/help", "/help", "result", state="done")]
    checkpoints = [{"turn_index": 0, "keep_uuid": None}]

    with pytest.raises(ValueError, match="user_uuid"):
        plan_rewind(records, checkpoints, checkpoints[0], "restore")


def test_rewind_uuid_map_must_cover_kept_state():
    plan = plan_rewind(_records(), _checkpoints(), _checkpoints()[1], "restore")

    with pytest.raises(ValueError, match="fork uuid map missing entries"):
        apply_rewind_uuid_map(plan, {"u1": "u1b"})


def test_branch_uuid_map_must_cover_full_state():
    with pytest.raises(ValueError, match="fork uuid map missing entries"):
        build_branch_plan(_records(), _checkpoints(), "a2", "a2", {"u1": "u1b"})


def test_non_string_checkpoint_user_uuid_raises():
    records = [UserRecord("first", "1")]
    checkpoints = [{"turn_index": 0, "user_uuid": 1, "keep_uuid": None}]

    with pytest.raises(ValueError, match="user_uuid"):
        plan_rewind(records, checkpoints, checkpoints[0], "restore")


def test_later_checkpoint_without_keep_uuid_raises():
    checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {"turn_index": 1, "user_uuid": "u2", "keep_uuid": None},
    ]

    with pytest.raises(ValueError, match="only the first checkpoint"):
        plan_rewind(_records(), checkpoints, checkpoints[1], "restore")


def test_remap_helpers_do_not_mutate_parent_records_or_checkpoints():
    records = _records()
    checkpoints = _checkpoints()

    remapped_records = remap_records(records, {"u1": "u1b"})
    remapped_checkpoints = remap_checkpoints(checkpoints, {"u1": "u1b"})

    assert records[0].uuid == "u1"
    assert checkpoints[0]["user_uuid"] == "u1"
    assert remapped_records[0].uuid == "u1b"
    assert remapped_checkpoints[0]["user_uuid"] == "u1b"


def test_branch_plan_remaps_full_state_without_mutating_parent():
    records = _records()
    checkpoints = _checkpoints()
    branch = build_branch_plan(records, checkpoints, "a2", "a2", {
        "u1": "u1b",
        "u2": "u2b",
        "a1": "a1b",
        "a2": "a2b",
    })

    assert [record.uuid for record in branch.records if isinstance(record, UserRecord)] == [
        "u1b",
        "u2b",
    ]
    assert branch.checkpoints == [
        {"turn_index": 0, "user_uuid": "u1b", "keep_uuid": None},
        {"turn_index": 1, "user_uuid": "u2b", "keep_uuid": "a1b"},
    ]
    assert branch.last_assistant_uuid == "a2b"
    assert branch.last_message_uuid == "a2b"
    assert records[0].uuid == "u1"
    assert checkpoints[1]["user_uuid"] == "u2"


def test_remap_uuid_preserves_unknown_values():
    assert remap_uuid("kept", {}) == "kept"
    assert remap_uuid(None, {"x": "y"}) is None
