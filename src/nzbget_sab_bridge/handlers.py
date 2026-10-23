"""Bridge-mode method dispatch, including the small mutation surface."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable

from .append import AppendSubmission, parse_append_request
from .config import Settings
from .rpc import (
    KNOWN_UNIMPLEMENTED_METHODS,
    InvalidAction,
    InvalidParams,
    InvalidProcedure,
    NotImplementedMethod,
    RpcError,
    RpcRequest,
)
from .sab.client import SabClient
from .sab.errors import (
    SabApiError,
    SabError,
    SabHttpError,
    SabTimeoutError,
    SabTransportError,
)
from .sab.models import SabAddResult, SabQueue
from .storage.idmap import IdMap, IdMapError
from .translate.categories import category_matches, resolve_category
from .translate.config import build_config
from .translate.dashboard import translate_status
from .translate.history import translate_history
from .translate.queue import translate_queue
from .translate.status import priority_to_sab, sab_priority_label

logger = logging.getLogger("nzbget_sab_bridge.mutations")

_PAUSE_METHODS = frozenset({"pause", "pausedownload", "pausedownload2"})
_RESUME_METHODS = frozenset({"resume", "resumedownload", "resumedownload2"})


async def dispatch(
    request: RpcRequest,
    *,
    settings: Settings,
    idmap: IdMap | None,
    sab: SabClient | None,
) -> object:
    method = request.method.lower()

    if method == "version":
        _no_params(request)
        return settings.advertised_nzbget_version
    if method == "config":
        _no_params(request)
        categories = await _sab_call(_require_sab(sab).categories())
        return build_config(categories.categories)
    if method == "configtemplates":
        _no_params(request)
        return []
    if method == "status":
        _no_params(request)
        client = _require_sab(sab)
        try:
            queue, status, server_stats = await asyncio.gather(
                client.queue(limit=500),
                client.status(),
                client.server_stats(),
            )
        except SabError as exc:
            raise RpcError(str(exc)) from None
        return translate_status(queue, status, server_stats)
    if method == "listgroups":
        _optional_int_param(request)
        client = _require_sab(sab)
        queue = await _sab_call(client.queue(limit=500))
        ids = await _allocate_ids(idmap, [slot.nzo_id for slot in queue.slots], state="queue")
        return translate_queue(queue, ids)
    if method == "history":
        hidden = _optional_bool_param(request)
        if hidden:
            raise RpcError("Hidden history is not supported")
        client = _require_sab(sab)
        history = await _sab_call(client.history(limit=500))
        final_slots = [
            slot for slot in history.slots if slot.status.strip().lower() in {"completed", "failed"}
        ]
        ids = await _allocate_ids(idmap, [slot.nzo_id for slot in final_slots], state="history")
        return translate_history(final_slots, ids)
    if method in _PAUSE_METHODS:
        _no_params(request)
        return await _set_queue_paused(_require_sab(sab), paused=True)
    if method in _RESUME_METHODS:
        _no_params(request)
        return await _set_queue_paused(_require_sab(sab), paused=False)
    if method == "editqueue":
        return await _editqueue(request, idmap=idmap, sab=_require_sab(sab))
    if method == "rate":
        rate = _rate_param(request)
        return await _set_rate(_require_sab(sab), rate)
    if method in {"append", "appendurl"}:
        return await _append(
            request,
            method=method,
            settings=settings,
            idmap=idmap,
            sab=_require_sab(sab),
        )

    if method in KNOWN_UNIMPLEMENTED_METHODS:
        raise NotImplementedMethod(method)
    raise InvalidProcedure()


async def _set_queue_paused(client: SabClient, *, paused: bool) -> bool:
    try:
        result = await (client.pause_queue() if paused else client.resume_queue())
    except (SabTimeoutError, SabTransportError, SabHttpError):
        # The write may have been applied; reconcile with a read-only status.
        try:
            status = await client.status()
        except SabError:
            raise RpcError("Queue pause outcome unknown") from None
        if bool(status.paused) == paused:
            return True
        raise RpcError("Queue pause outcome unknown") from None
    except SabError as exc:
        raise RpcError(str(exc)) from None
    if not result.status:
        raise RpcError("SABnzbd rejected queue pause/resume")
    return True


async def _editqueue(request: RpcRequest, *, idmap: IdMap | None, sab: SabClient) -> bool:
    if len(request.params) != 3:
        raise InvalidParams("Invalid parameter count")
    command, parameter, raw_ids = request.params
    if not isinstance(command, str):
        raise InvalidParams("Invalid parameter (Command)")
    command_key = command.casefold()
    if command_key not in {
        "groupsetcategory",
        "groupsetpriority",
        "grouppause",
        "groupresume",
    }:
        raise InvalidAction(f"Unsupported editqueue command: {command}")
    if not isinstance(parameter, str):
        raise InvalidParams("Invalid parameter (Param)")
    ids = _parse_ids(raw_ids)
    target_priority = _parse_priority(parameter) if command_key == "groupsetpriority" else None

    nzo_ids, queue = await _resolve_queued_targets(idmap, sab, ids)

    if command_key == "groupsetcategory":
        try:
            categories = await sab.categories()
        except SabError as exc:
            raise RpcError(str(exc)) from None
        try:
            target_category = resolve_category(parameter, categories.categories)
        except ValueError as exc:
            raise InvalidParams(str(exc)) from None
        return await _change_category(sab, nzo_ids, target_category)

    if command_key == "grouppause":
        return await _set_jobs_paused(sab, nzo_ids, queue=queue, paused=True)
    if command_key == "groupresume":
        return await _set_jobs_paused(sab, nzo_ids, queue=queue, paused=False)

    assert target_priority is not None
    return await _change_priority(sab, nzo_ids, queue=queue, target_priority=target_priority)


async def _resolve_queued_targets(
    idmap: IdMap | None,
    sab: SabClient,
    ids: list[int],
) -> tuple[list[str], SabQueue]:
    if idmap is None:
        raise RpcError("Storage unavailable")
    try:
        nzo_ids = await asyncio.to_thread(_resolve_nzo_ids, idmap, ids)
    except IdMapError:
        raise RpcError("Storage unavailable") from None

    try:
        queue = await sab.queue(limit=500)
    except SabError as exc:
        raise RpcError(str(exc)) from None

    queued = {slot.nzo_id for slot in queue.slots}
    for nzb_id, nzo_id in zip(ids, nzo_ids, strict=True):
        if nzo_id not in queued:
            raise RpcError(f"NZBID {nzb_id} is not currently queued")
    return nzo_ids, queue


async def _change_category(client: SabClient, nzo_ids: list[str], target_category: str) -> bool:
    # All validation is complete. From here on, writes may produce partial
    # state; we never issue compensating writes.
    for nzo_id in nzo_ids:
        try:
            await client.change_category([nzo_id], target_category)
        except SabError:
            # The write may have timed out after dispatch. The final read
            # below is the source of truth; do not retry.
            break

    try:
        after = await client.queue(limit=500)
    except SabError:
        raise RpcError("Category change outcome unknown") from None

    after_categories = {slot.nzo_id: slot.cat for slot in after.slots}
    matched = sum(
        1
        for nzo_id in nzo_ids
        if nzo_id in after_categories
        and category_matches(after_categories[nzo_id], target_category)
    )
    if matched == len(nzo_ids):
        return True
    if matched:
        logger.warning(
            "category change partially applied",
            extra={"component": "mutations", "matched": matched, "total": len(nzo_ids)},
        )
        raise RpcError(f"Category change partially applied ({matched}/{len(nzo_ids)})")
    logger.warning(
        "category change failed",
        extra={"component": "mutations", "matched": 0, "total": len(nzo_ids)},
    )
    raise RpcError("Category change failed")


async def _set_jobs_paused(
    client: SabClient,
    nzo_ids: list[str],
    *,
    queue: SabQueue,
    paused: bool,
) -> bool:
    action = "Pause" if paused else "Resume"
    slots = {slot.nzo_id: slot for slot in queue.slots}
    if paused:
        force = [
            nzo_id for nzo_id in nzo_ids if slots[nzo_id].priority.strip().casefold() == "force"
        ]
        if force:
            raise RpcError("Cannot pause force-priority jobs; change priority first")

    completed = 0
    uncertain = False
    for nzo_id in nzo_ids:
        try:
            result = await (client.pause_jobs([nzo_id]) if paused else client.resume_jobs([nzo_id]))
        except SabApiError:
            break
        except SabError:
            # Timeout, transport failure, or malformed success envelope: the
            # write may have been applied, so reconcile instead of retrying.
            uncertain = True
            break
        if not result.status:
            break
        completed += 1

    if uncertain:
        try:
            after = await client.queue(limit=500)
        except SabError:
            raise RpcError(f"{action} outcome unknown") from None
        after_slots = {slot.nzo_id: slot for slot in after.slots}
        if paused:
            matched = sum(
                1
                for nzo_id in nzo_ids
                if nzo_id in after_slots
                and after_slots[nzo_id].status.strip().casefold() == "paused"
            )
        else:
            try:
                status = await client.status()
            except SabError:
                raise RpcError("Resume outcome unknown") from None
            if status.paused:
                # SAB masks individual job status while the global queue is
                # paused, so a resume cannot be proven from a queue read.
                raise RpcError("Resume outcome unknown while global pause is active")
            matched = sum(
                1
                for nzo_id in nzo_ids
                if nzo_id in after_slots
                and after_slots[nzo_id].status.strip().casefold() != "paused"
            )
        if matched == len(nzo_ids):
            return True
        if matched:
            raise RpcError(f"{action} partially applied ({matched}/{len(nzo_ids)})")
        raise RpcError(f"{action} outcome unknown")

    if completed == len(nzo_ids):
        return True
    if completed:
        raise RpcError(f"{action} partially applied ({completed}/{len(nzo_ids)})")
    raise RpcError(f"{action} failed")


async def _change_priority(
    client: SabClient,
    nzo_ids: list[str],
    *,
    queue: SabQueue,
    target_priority: int,
) -> bool:
    target_sab = priority_to_sab(target_priority)
    target_label = sab_priority_label(target_sab).casefold()
    slots = {slot.nzo_id: slot for slot in queue.slots}
    changing = [
        nzo_id for nzo_id in nzo_ids if slots[nzo_id].priority.strip().casefold() != target_label
    ]
    if not changing:
        return True

    # SAB reports individual paused status separately from the global pause
    # flag. Its priority API, however, clears an individual pause before it
    # returns: NzbQueue.__set_priority sets PAUSED jobs to QUEUED. There is no
    # atomic priority-and-pause operation, so re-pausing would leave a window
    # in which the job can start. Reject before writing instead.
    paused_ids = {
        nzo_id for nzo_id in changing if slots[nzo_id].status.strip().casefold() == "paused"
    }
    if target_sab == 2 and paused_ids:
        raise RpcError("Cannot set Force priority on a paused job")
    if paused_ids:
        raise RpcError(
            "SAB cannot change priority of an individually paused job without clearing "
            "its pause; resume the job first"
        )

    for nzo_id in nzo_ids:
        if slots[nzo_id].priority.strip().casefold() == target_label:
            continue
        try:
            result = await client.set_priority([nzo_id], target_sab)
        except SabApiError:
            break
        except SabError:
            # The write may have been applied. Do not retry or compensate.
            break
        if result.position < 0:
            break

    try:
        after = await client.queue(limit=500)
    except SabError:
        raise RpcError("Priority change outcome unknown") from None

    after_slots = {slot.nzo_id: slot for slot in after.slots}
    matched = sum(
        1
        for nzo_id in nzo_ids
        if nzo_id in after_slots and after_slots[nzo_id].priority.strip().casefold() == target_label
    )
    total = len(nzo_ids)
    if matched == total:
        return True
    if matched:
        logger.warning(
            "priority change partially applied",
            extra={"component": "mutations", "matched": matched, "total": total},
        )
        raise RpcError(f"Priority change partially applied ({matched}/{total})")
    logger.warning(
        "priority change failed",
        extra={"component": "mutations", "matched": 0, "total": total},
    )
    raise RpcError("Priority change failed")


def _parse_priority(raw_priority: str) -> int:
    value = raw_priority.strip()
    if not value:
        raise InvalidParams("Invalid parameter (Priority)")
    try:
        priority = int(value, 10)
    except ValueError:
        raise InvalidParams("Invalid parameter (Priority)") from None
    if not -2_147_483_648 <= priority <= 2_147_483_647:
        raise InvalidParams("Invalid parameter (Priority)")
    return priority


def _rate_param(request: RpcRequest) -> int:
    if len(request.params) != 1:
        raise InvalidParams("Invalid parameter count")
    value = request.params[0]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2_147_483_647:
        raise InvalidParams("Invalid parameter (Rate)")
    return value


async def _set_rate(client: SabClient, kilobytes_per_second: int) -> bool:
    expected = kilobytes_per_second * 1024
    uncertain = False
    try:
        result = await client.set_speedlimit(kilobytes_per_second)
    except SabApiError as exc:
        raise RpcError(str(exc)) from None
    except SabError:
        uncertain = True
    else:
        if not result.status:
            raise RpcError("SABnzbd rejected the rate limit")

    try:
        status = await client.status()
    except SabError:
        raise RpcError("Rate limit outcome unknown") from None
    actual = _speedlimit_bytes(status.speedlimit_abs)
    if actual is not None and actual == expected:
        return True
    if uncertain or actual is None:
        raise RpcError("Rate limit outcome unknown")
    raise RpcError("Rate limit was not confirmed")


async def _append(
    request: RpcRequest,
    *,
    method: str,
    settings: Settings,
    idmap: IdMap | None,
    sab: SabClient,
) -> int:
    if idmap is None:
        raise RpcError("Storage unavailable")
    submission = parse_append_request(request, method=method, settings=settings)

    try:
        categories = await sab.categories()
    except SabError as exc:
        raise RpcError(str(exc)) from None
    try:
        category = resolve_category(submission.category, categories.categories)
    except ValueError as exc:
        raise InvalidParams(str(exc)) from None

    try:
        result = await _submit(sab, submission, category)
    except SabApiError as exc:
        raise RpcError(str(exc)) from None
    except SabError:
        raise RpcError(
            "Submission outcome unknown; SABnzbd may have accepted it. "
            "Check the queue before resubmitting."
        ) from None

    return await _persist_submission_id(idmap, result.nzo_ids)


async def _submit(
    client: SabClient,
    submission: AppendSubmission,
    category: str,
) -> SabAddResult:
    if submission.kind == "file":
        assert submission.file_content is not None
        return await client.add_file(
            submission.file_content,
            submission.filename,
            category=category,
            priority=submission.sab_priority,
        )
    assert submission.url is not None
    return await client.add_url(
        submission.url,
        nzbname=submission.filename,
        category=category,
        priority=submission.sab_priority,
    )


async def _persist_submission_id(idmap: IdMap, nzo_ids: list[str]) -> int:
    if not nzo_ids:
        raise RpcError(
            "SABnzbd accepted the submission but returned no job ID; "
            "the outcome is unresolved. Check the queue before resubmitting."
        )
    if len(nzo_ids) != 1:
        raise RpcError(
            f"SABnzbd accepted the submission as {len(nzo_ids)} jobs; "
            "append requires exactly one NZBID. The outcome is unresolved."
        )
    nzo_id = nzo_ids[0]
    try:
        return await asyncio.to_thread(idmap.allocate, nzo_id, state="queue")
    except (IdMapError, ValueError):
        raise RpcError(
            "SABnzbd accepted the submission but the bridge could not persist "
            "a stable NZBID; the outcome is unresolved. Check the queue before "
            "resubmitting."
        ) from None


def _speedlimit_bytes(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    try:
        return int(float(value.strip()))
    except ValueError:
        return None


def _parse_ids(raw_ids: object) -> list[int]:
    if not isinstance(raw_ids, list) or not raw_ids:
        raise InvalidParams("Invalid parameter (IDs)")
    deduplicated: list[int] = []
    for value in raw_ids:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise InvalidParams("Invalid parameter (IDs)")
        if value not in deduplicated:
            deduplicated.append(value)
    return deduplicated


def _resolve_nzo_ids(idmap: IdMap, ids: list[int]) -> list[str]:
    resolved: list[str] = []
    for nzb_id in ids:
        nzo_id = idmap.get_nzo_id(nzb_id)
        if nzo_id is None:
            raise RpcError(f"Unknown NZBID {nzb_id}")
        resolved.append(nzo_id)
    return resolved


def _no_params(request: RpcRequest) -> None:
    if request.params:
        raise InvalidParams("Invalid parameter count")


def _optional_int_param(request: RpcRequest) -> None:
    if not request.params:
        return
    if len(request.params) != 1 or not isinstance(request.params[0], int):
        raise InvalidParams("Invalid parameter count")


def _optional_bool_param(request: RpcRequest) -> bool:
    if not request.params:
        return False
    if len(request.params) != 1 or not isinstance(request.params[0], bool):
        raise InvalidParams("Invalid parameter (Hidden)")
    return request.params[0]


def _require_sab(sab: SabClient | None) -> SabClient:
    if sab is None:
        raise RpcError("SABnzbd unavailable")
    return sab


async def _sab_call[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except SabError as exc:
        raise RpcError(str(exc)) from None


async def _allocate_ids(idmap: IdMap | None, nzo_ids: list[str], *, state: str) -> list[int]:
    if idmap is None:
        raise RpcError("Storage unavailable")
    try:
        return await asyncio.to_thread(idmap.allocate_many, nzo_ids, state=state)
    except IdMapError:
        raise RpcError("Storage unavailable") from None
