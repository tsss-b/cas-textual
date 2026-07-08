from copy import deepcopy
from dataclasses import dataclass

from castcode.records import Record, UserRecord


@dataclass(frozen=True)
class RewindPlan:
    fork_uuid: str | None
    fresh_session: bool
    records_before_remap: list[Record]
    checkpoints_before_remap: list[dict]
    last_assistant_uuid: str | None
    last_message_uuid: str | None
    edit_text: str | None


@dataclass(frozen=True)
class AppliedRewindPlan:
    records: list[Record]
    checkpoints: list[dict]
    last_assistant_uuid: str | None
    last_message_uuid: str | None


@dataclass(frozen=True)
class BranchPlan:
    records: list[Record]
    checkpoints: list[dict]
    last_assistant_uuid: str | None
    last_message_uuid: str | None


def remap_uuid(uuid, uuid_map):
    if uuid is None:
        return None
    return uuid_map.get(uuid, uuid)


def remap_records(records, uuid_map):
    remapped = []
    for record in records:
        record = deepcopy(record)
        if isinstance(record, UserRecord):
            record.uuid = remap_uuid(record.uuid, uuid_map)
        remapped.append(record)
    return remapped


def remap_checkpoints(checkpoints, uuid_map):
    remapped = []
    for checkpoint in checkpoints:
        item = {
            **deepcopy(checkpoint),
            "user_uuid": remap_uuid(checkpoint.get("user_uuid"), uuid_map),
            "keep_uuid": remap_uuid(checkpoint.get("keep_uuid"), uuid_map),
        }
        if "assistant_uuid" in checkpoint:
            item["assistant_uuid"] = remap_uuid(
                checkpoint.get("assistant_uuid"),
                uuid_map,
            )
        remapped.append(item)
    return remapped


def plan_rewind(
    records,
    checkpoints,
    checkpoint,
    operation,
    *,
    edit_text=None,
) -> RewindPlan:
    if operation not in ("restore", "reload"):
        raise ValueError(f"unknown rewind operation: {operation!r}")
    turn_index = checkpoint.get("turn_index")
    if not isinstance(turn_index, int):
        raise ValueError("checkpoint turn_index must be an int")
    if turn_index < 0 or turn_index >= len(checkpoints):
        raise ValueError("checkpoint turn_index is out of range")
    if checkpoints[turn_index] != checkpoint:
        raise ValueError("checkpoint turn_index does not match checkpoint list")
    user_uuid = checkpoint.get("user_uuid")
    if not isinstance(user_uuid, str) or not user_uuid:
        raise ValueError("checkpoint user_uuid must be a non-empty string")
    keep_uuid = checkpoint.get("keep_uuid")
    if keep_uuid is None and turn_index != 0:
        raise ValueError("only the first checkpoint may have no keep_uuid")

    record_index, selected = _find_user_record(records, user_uuid)
    kept_records = deepcopy(list(records[:record_index]))
    kept_checkpoints = deepcopy(list(checkpoints[:turn_index]))
    _validate_checkpoint_users(kept_records, kept_checkpoints)
    return RewindPlan(
        fork_uuid=keep_uuid,
        fresh_session=keep_uuid is None,
        records_before_remap=kept_records,
        checkpoints_before_remap=kept_checkpoints,
        last_assistant_uuid=checkpoint.get("assistant_uuid", keep_uuid),
        last_message_uuid=keep_uuid,
        edit_text=(edit_text if edit_text is not None else selected.text)
        if operation == "reload"
        else None,
    )


def apply_rewind_uuid_map(plan: RewindPlan, uuid_map) -> AppliedRewindPlan:
    if not plan.fresh_session:
        _require_uuid_map(
            plan.records_before_remap,
            plan.checkpoints_before_remap,
            plan.last_assistant_uuid,
            plan.last_message_uuid,
            uuid_map,
        )
    return AppliedRewindPlan(
        records=remap_records(plan.records_before_remap, uuid_map),
        checkpoints=remap_checkpoints(plan.checkpoints_before_remap, uuid_map),
        last_assistant_uuid=remap_uuid(plan.last_assistant_uuid, uuid_map),
        last_message_uuid=remap_uuid(plan.last_message_uuid, uuid_map),
    )


def build_branch_plan(
    records,
    checkpoints,
    last_assistant_uuid,
    last_message_uuid,
    uuid_map,
) -> BranchPlan:
    _require_uuid_map(records, checkpoints, last_assistant_uuid, last_message_uuid, uuid_map)
    return BranchPlan(
        records=remap_records(records, uuid_map),
        checkpoints=remap_checkpoints(checkpoints, uuid_map),
        last_assistant_uuid=remap_uuid(last_assistant_uuid, uuid_map),
        last_message_uuid=remap_uuid(last_message_uuid, uuid_map),
    )


def _find_user_record(records, user_uuid):
    for index, record in enumerate(records):
        if isinstance(record, UserRecord) and record.uuid == user_uuid:
            return index, record
    raise ValueError(f"selected user record not found: {user_uuid!r}")


def _validate_checkpoint_users(records, checkpoints) -> None:
    user_uuids = {
        record.uuid
        for record in records
        if isinstance(record, UserRecord) and record.uuid is not None
    }
    for checkpoint in checkpoints:
        if checkpoint.get("user_uuid") not in user_uuids:
            raise ValueError("checkpoint references a missing user record")


def _require_uuid_map(
    records,
    checkpoints,
    last_assistant_uuid,
    last_message_uuid,
    uuid_map,
) -> None:
    required = {
        record.uuid
        for record in records
        if isinstance(record, UserRecord) and record.uuid is not None
    }
    for checkpoint in checkpoints:
        for key in ("user_uuid", "keep_uuid", "assistant_uuid"):
            value = checkpoint.get(key)
            if value is not None:
                required.add(value)
    if last_assistant_uuid is not None:
        required.add(last_assistant_uuid)
    if last_message_uuid is not None:
        required.add(last_message_uuid)
    missing = sorted(uuid for uuid in required if uuid not in uuid_map)
    if missing:
        raise ValueError(f"fork uuid map missing entries: {', '.join(missing)}")
