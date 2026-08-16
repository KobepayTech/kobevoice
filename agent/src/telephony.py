"""Telephony behaviours shared by inbound and outbound call agents.

The function tools here are the ones a call-center agent actually needs mid-call:
hand off to a human, hang up cleanly, hang up on voicemail, and honour a
do-not-call request the moment the caller voices it.

Adapted from livekit-examples/outbound-caller-python (MIT).
"""

from __future__ import annotations

import logging
import os

import httpx
from livekit import api
from livekit.agents import Agent, JobContext, RunContext, function_tool

logger = logging.getLogger("kobevoice.telephony")

API_BASE = os.getenv("KOBEOS_API_BASE", "http://localhost:8000")
TENANT_SLUG = os.getenv("KOBEOS_TENANT_SLUG", "")


def _headers() -> dict[str, str]:
    return {"X-Tenant-Slug": TENANT_SLUG}


async def check_dialable(e164: str, *, require_consent: bool = True) -> tuple[bool, str]:
    """Ask the control plane whether this number may be dialled.

    Fails **closed**: if the compliance service is unreachable we refuse the call
    rather than dial without a check. An outage should cost us calls, not
    generate violations.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{API_BASE}/compliance/check-dialable",
                json={"e164": e164, "require_consent": require_consent},
                headers=_headers(),
            )
            r.raise_for_status()
            body = r.json()
            return bool(body["allowed"]), str(body.get("reason", "unknown"))
    except Exception as exc:  # noqa: BLE001 - any failure must block the dial
        logger.error("compliance check failed for %s, refusing to dial: %s", e164, exc)
        return False, "compliance_check_unavailable"


async def record_dnc(e164: str) -> bool:
    """Add a number to the tenant do-not-call list."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{API_BASE}/compliance/dnc", json={"e164": e164}, headers=_headers()
            )
            r.raise_for_status()
            return True
    except Exception as exc:  # noqa: BLE001
        # Losing a DNC request is the worst failure in this file — surface loudly.
        logger.error("FAILED to record DNC for %s: %s", e164, exc)
        return False


async def post_transcript_turn(call_id: str, speaker: str, text: str, offset: float) -> None:
    """Stream a transcript turn to the dashboard. Best-effort by design."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{API_BASE}/calls/{call_id}/transcript",
                json={"speaker": speaker, "text": text, "offset_seconds": offset},
                headers=_headers(),
            )
    except Exception as exc:  # noqa: BLE001
        # A dropped transcript line must never interrupt a live call.
        logger.warning("transcript post failed: %s", exc)


class CallCenterAgent(Agent):
    """Agent with the call-control tools a phone conversation requires."""

    def __init__(
        self,
        *,
        instructions: str,
        job_ctx: JobContext,
        participant_identity: str | None = None,
        transfer_to: str | None = None,
        caller_number: str | None = None,
    ) -> None:
        super().__init__(instructions=instructions)
        self._job_ctx = job_ctx
        self._participant_identity = participant_identity
        self._transfer_to = transfer_to
        self._caller_number = caller_number

    async def hangup(self) -> None:
        """Delete the room, which drops every participant including the SIP leg."""
        try:
            await self._job_ctx.api.room.delete_room(
                api.DeleteRoomRequest(room=self._job_ctx.room.name)
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("hangup failed: %s", exc)

    @function_tool()
    async def transfer_call(self, ctx: RunContext) -> str:
        """Transfer the caller to a human agent. Confirm with the caller first."""
        if not self._transfer_to:
            return "No human agent is available to transfer to."
        if not self._participant_identity:
            return "Cannot transfer: no active caller."

        # Let the handoff sentence finish playing before the leg is moved,
        # otherwise the caller hears the transfer cut them off mid-word.
        await ctx.session.generate_reply(
            instructions="Tell the user you are transferring them to a colleague now."
        )
        try:
            await self._job_ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    room_name=self._job_ctx.room.name,
                    participant_identity=self._participant_identity,
                    transfer_to=f"tel:{self._transfer_to}",
                )
            )
            logger.info("transferred call to %s", self._transfer_to)
            return "Transfer complete."
        except Exception as exc:  # noqa: BLE001
            logger.error("transfer failed: %s", exc)
            await ctx.session.generate_reply(
                instructions="Apologise: the transfer failed. Offer to take a message."
            )
            return "Transfer failed."

    @function_tool()
    async def end_call(self, ctx: RunContext) -> str:
        """End the call once the conversation is finished."""
        await ctx.session.generate_reply(instructions="Say a brief goodbye.")
        await self.hangup()
        return "Call ended."

    @function_tool()
    async def detected_answering_machine(self, ctx: RunContext) -> str:
        """Call this after hearing a voicemail greeting rather than a person."""
        logger.info("voicemail detected; hanging up")
        await self.hangup()
        return "Voicemail detected; call ended."

    @function_tool()
    async def add_to_do_not_call(self, ctx: RunContext) -> str:
        """Honour a caller's request never to be contacted again.

        Recorded immediately, before the call ends — a DNC request lost to a
        dropped call is exactly the failure regulators penalise.
        """
        number = self._caller_number
        if not number:
            return "Could not identify the number to suppress."
        ok = await record_dnc(number)
        if not ok:
            # Tell the truth rather than falsely reassuring the caller.
            return "Could not confirm the do-not-call request; escalate to a human."
        await ctx.session.generate_reply(
            instructions="Confirm they have been removed and will not be called again."
        )
        return "Number added to do-not-call list."
