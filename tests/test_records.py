from castcode.records import (
    AssistantRecord,
    BlockPreview,
    NoticeRecord,
    ToolRecord,
    UserRecord,
)


def test_record_equality_and_defaults():
    assert UserRecord("hello") == UserRecord("hello", uuid=None)
    assert AssistantRecord("reply").text == "reply"
    assert NoticeRecord("notice").text == "notice"
    assert ToolRecord("Read", "Read").detail == ""
    assert ToolRecord("Read", "Read").state == "running"
    assert ToolRecord("Read", "Read").subtools == []
    assert ToolRecord("Read", "Read").error == ""
    assert ToolRecord("Read", "Read").detail_error is False


def test_block_preview_shape():
    preview = BlockPreview("3 lines", "alpha")
    assert preview.metrics == "3 lines"
    assert preview.text == "alpha"
    assert tuple(preview) == ("3 lines", "alpha")
