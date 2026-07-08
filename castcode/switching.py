from json import JSONDecodeError

from castcode.records import NoticeRecord
from castcode.interaction import PERMISSION_MODES, full_idle
from castcode.session import Session
from castcode.sidecar import read_sidecar
from castcode.state import Conversation
from castcode.ui.input import Prompt
from castcode.ui.layout import Chat


CORRUPT_VIEW = "saved view is corrupt - resuming context only"
MISSING_VIEW = "no saved view for this session - resuming context only"
MISSING_SESSION = "saved session no longer on disk - resuming with empty context"


async def switch_to(app, new_conv: Conversation, check_session=False) -> None:
    old = app.conversation
    chat = app.query_one("#chat", Chat)
    prompt = app.query_one(Prompt)
    if full_idle(app.interaction) or app.interaction.transaction == "switch":
        app._snapshot_transcript(old)
        app._write_sidecar(old)
    if old.connect_worker is not None:
        await old.connect_worker.wait()
    await old.session.disconnect()
    if check_session:
        _apply_missing_session_notice(new_conv)
    app.conversation = new_conv
    app._connect_worker = app.connect()
    await chat.rebuild(new_conv.transcript)
    prompt.clear()
    if new_conv.draft:
        prompt.insert(new_conv.draft)
    app._update_status_line()
    app._refresh_prompt_commands()
    app._refresh_todos()
    app._stick(chat)


async def resume_cold(app, row: dict) -> None:
    conv = Conversation(session=Session())
    conv.session_id = row["session_id"]
    check_session = False
    path = conv.session.sidecar_path(conv.session_id)
    if path.exists():
        try:
            envelope = read_sidecar(path)
        except (OSError, ValueError, JSONDecodeError):
            conv.transcript = [NoticeRecord(CORRUPT_VIEW)]
        else:
            check_session = envelope.token is not None
            conv.transcript = envelope.records
            conv.checkpoints = envelope.checkpoints
            conv.last_assistant_uuid = envelope.last_assistant_uuid
            conv.last_message_uuid = envelope.last_message_uuid
            conv.draft = envelope.draft
            if envelope.model_id is not None:
                conv.model_id = envelope.model_id
            if envelope.model is not None:
                conv.model = envelope.model
            if envelope.permission_mode in PERMISSION_MODES:
                conv.permission_mode = envelope.permission_mode
    else:
        conv.transcript = [NoticeRecord(MISSING_VIEW)]
    await switch_to(app, conv, check_session=check_session)


def _apply_missing_session_notice(conv: Conversation) -> None:
    live = conv.session.session_last_modified(conv.session_id)
    if live is None:
        conv.transcript.append(NoticeRecord(MISSING_SESSION))
        conv.session_id = None
        conv.checkpoints.clear()
        conv.last_assistant_uuid = None
        conv.last_message_uuid = None
