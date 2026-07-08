import json

import pytest

from castcode.records import (
    AssistantRecord,
    BlockPreview,
    NoticeRecord,
    ToolRecord,
    UserRecord,
)
from castcode.sidecar import dump_records, load_records, read_sidecar, write_sidecar


def test_v1_load_with_no_checkpoints():
    env = load_records(json.dumps({
        "version": 1,
        "token": 123,
        "records": [{"__type__": "user", "text": "hi", "uuid": "u1"}],
    }))

    assert env.version == 1
    assert env.token == 123
    assert env.records == [UserRecord("hi", "u1")]
    assert env.checkpoints == []


def test_v1_load_with_additive_fields():
    env = load_records(json.dumps({
        "version": 1,
        "token": 1,
        "records": [],
        "last_assistant_uuid": "a1",
        "last_message_uuid": "u2",
        "model_id": "m1",
        "model": "Model One",
    }))

    assert env.last_assistant_uuid == "a1"
    assert env.last_message_uuid == "u2"
    assert env.model_id == "m1"
    assert env.model == "Model One"


def test_v1_missing_additive_fields_default_to_none():
    env = load_records(json.dumps({"version": 1, "token": 1, "records": []}))

    assert env.last_assistant_uuid is None
    assert env.last_message_uuid is None
    assert env.model_id is None
    assert env.model is None


def test_v2_load_with_checkpoints():
    checkpoint = {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}
    env = load_records(json.dumps({
        "version": 2,
        "token": 1,
        "records": [],
        "checkpoints": [checkpoint],
    }))

    assert env.checkpoints == [checkpoint]


def test_v2_load_with_empty_checkpoints():
    env = load_records(json.dumps({
        "version": 2,
        "token": 1,
        "records": [],
        "checkpoints": [],
    }))

    assert env.checkpoints == []


@pytest.mark.parametrize(
    "checkpoint, message",
    [
        (None, "checkpoint must be an object"),
        ({"turn_index": "0", "user_uuid": "u1", "keep_uuid": None}, "turn_index"),
        ({"turn_index": 0, "keep_uuid": None}, "user_uuid"),
        ({"turn_index": 0, "user_uuid": "u1", "keep_uuid": 123}, "keep_uuid"),
        (
            {
                "turn_index": 0,
                "user_uuid": "u1",
                "keep_uuid": None,
                "assistant_uuid": 123,
            },
            "assistant_uuid",
        ),
    ],
)
def test_malformed_checkpoints_raise(checkpoint, message):
    with pytest.raises(ValueError, match=message):
        load_records(json.dumps({
            "version": 2,
            "token": 1,
            "records": [],
            "checkpoints": [checkpoint],
        }))


def test_writer_emits_v1_for_empty_checkpoints():
    data = json.loads(dump_records([], 1, []))

    assert data["version"] == 1
    assert "checkpoints" not in data


def test_writer_emits_v2_for_non_empty_checkpoints():
    data = json.loads(dump_records(
        [],
        1,
        [{"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}],
    ))

    assert data["version"] == 2
    assert data["checkpoints"] == [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}
    ]


def test_token_none_round_trips():
    env = load_records(dump_records([NoticeRecord("notice")], None))

    assert env.token is None
    assert env.records == [NoticeRecord("notice")]


def test_legacy_optional_fields():
    env = load_records(json.dumps({
        "version": 1,
        "token": 1,
        "records": [
            {"__type__": "user", "text": "legacy"},
            {
                "__type__": "tool",
                "tool_name": "Read",
                "title": "Read",
                "detail": "path",
                "preview": "",
                "state": "done",
                "subtools": [],
            },
        ],
    }))

    assert env.records == [
        UserRecord("legacy", None),
        ToolRecord("Read", "Read", "path", "", "done"),
    ]


def test_unknown_version_raises():
    with pytest.raises(ValueError, match="unknown sidecar version"):
        load_records(json.dumps({"version": 99, "token": 1, "records": []}))


def test_unknown_preview_tag_raises():
    with pytest.raises(ValueError, match="unknown preview tag"):
        load_records(json.dumps({
            "version": 1,
            "token": 1,
            "records": [
                {
                    "__type__": "tool",
                    "tool_name": "Read",
                    "title": "Read",
                    "preview": {"__preview__": "mystery"},
                }
            ],
        }))


def test_change_preview_round_trip():
    rows = [{"kind": "add", "line": "1", "marker": "+", "text": "alpha"}]
    env = load_records(dump_records([ToolRecord("Write", "Write", preview=rows)], 1))

    assert env.records == [ToolRecord("Write", "Write", preview=rows)]


def test_block_preview_round_trip():
    env = load_records(dump_records([
        ToolRecord("Read", "Read", preview=BlockPreview("2 lines", "alpha\nbeta"))
    ], 1))

    assert env.records == [
        ToolRecord("Read", "Read", preview=BlockPreview("2 lines", "alpha\nbeta"))
    ]


def test_record_types_round_trip():
    records = [
        UserRecord("hi", "u1"),
        AssistantRecord("reply"),
        NoticeRecord("notice"),
        ToolRecord(
            "Bash",
            "Bash",
            "pytest",
            "ok",
            "error",
            [{"state": "done", "title": "Read", "detail": "file"}],
            "boom",
            True,
        ),
    ]

    assert load_records(dump_records(records, 7)).records == records


def test_subtools_must_be_json_native():
    class NotJson:
        pass

    with pytest.raises(TypeError):
        dump_records([ToolRecord("Agent", "Agent", subtools=[NotJson()])], 1)


def test_atomic_write_creates_parent_and_replaces_final_path(tmp_path):
    path = tmp_path / "nested" / "session.json"

    write_sidecar(path, 1, [UserRecord("one")], [], None, None, None, None)
    write_sidecar(path, 2, [UserRecord("two")], [], "a1", "u2", "m1", "Model")

    env = read_sidecar(path)
    assert env.token == 2
    assert env.records == [UserRecord("two")]
    assert env.last_assistant_uuid == "a1"
    assert env.last_message_uuid == "u2"
    assert env.model_id == "m1"
    assert env.model == "Model"
    assert not (path.parent / ".session.json.tmp").exists()


def test_write_read_round_trip_with_checkpoints(tmp_path):
    path = tmp_path / "session.json"
    checkpoint = {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}

    write_sidecar(
        path,
        12,
        [UserRecord("hi", "u1")],
        [checkpoint],
        None,
        None,
        None,
        None,
    )

    env = read_sidecar(path)
    assert env.version == 2
    assert env.token == 12
    assert env.records == [UserRecord("hi", "u1")]
    assert env.checkpoints == [checkpoint]


def test_permission_mode_and_draft_round_trip():
    env = load_records(dump_records([], None, permission_mode="plan", draft="unsent"))
    assert env.permission_mode == "plan"
    assert env.draft == "unsent"


def test_permission_mode_and_draft_default_when_absent():
    env = load_records('{"version": 1, "records": []}')
    assert env.permission_mode is None
    assert env.draft == ""


def test_non_string_permission_mode_raises():
    with pytest.raises(ValueError, match="permission_mode"):
        load_records('{"version": 1, "records": [], "permission_mode": 3}')
