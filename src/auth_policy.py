"""Transport-independent authorization. Unlisted operations are never allowed."""

import logging
from dataclasses import dataclass, field
from enum import StrEnum

log = logging.getLogger("vauxr.authz")


class Role(StrEnum):
    OWNER = "owner"
    INTEGRATION = "integration"
    DEVICE = "device"


class Operation(StrEnum):
    DEVICES_LIST = "devices.list"
    DEVICE_CONFIG = "device.configure"
    SPEECH_CONFIG = "speech.configure"
    ANNOUNCE = "device.announce"
    CONTROL = "device.control"
    PLAYBACK = "device.playback"
    FIRMWARE_INITIATE = "firmware.initiate"
    FIRMWARE_READ = "firmware.read"
    FIRMWARE_PUBLISH = "firmware.publish"
    CHANNEL_LIST = "channel.list"
    CHANNEL_CONFIG = "channel.configure"
    CREDENTIAL_CREATE = "credential.create"
    CREDENTIAL_DISCLOSE = "credential.disclose"
    CREDENTIAL_ROTATE = "credential.rotate"
    CREDENTIAL_REVOKE = "credential.revoke"
    SERVER_MANAGE = "server.manage"
    OWNER_ADMIN = "owner.admin"
    WEBHOOK_CONFIG = "webhook.configure"
    PAIR_INITIATE = "pair.initiate"
    PAIR_APPROVE = "pair.approve"
    DEVICE_CONNECT = "device.connect"
    DEVICE_AUDIO = "device.audio"
    DEVICE_CONTROL = "device.self_control"
    DEVICE_BUTTON = "device.button"
    REALTIME_OFFER = "realtime.offer"
    CHANNEL_CONNECT = "channel.connect"
    VOICE_RESPONSE = "voice.respond"


@dataclass(frozen=True)
class Principal:
    role: Role
    subject: str
    credential_id: str
    # Opaque verifier-derived identity, not a bearer credential or session epoch.
    # Policy-only principals may omit it; the store then rejects current().
    credential_generation: str = field(default="", repr=False)


_DEVICE = frozenset(
    {
        Operation.DEVICE_CONNECT,
        Operation.DEVICE_AUDIO,
        Operation.DEVICE_CONTROL,
        Operation.DEVICE_BUTTON,
        Operation.REALTIME_OFFER,
    }
)
_INTEGRATION = frozenset(
    {
        Operation.DEVICES_LIST,
        Operation.ANNOUNCE,
        Operation.CONTROL,
        Operation.PLAYBACK,
        Operation.FIRMWARE_INITIATE,
        Operation.PAIR_INITIATE,
        Operation.PAIR_APPROVE,
        Operation.CHANNEL_CONNECT,
        Operation.VOICE_RESPONSE,
    }
)
_OWNER = frozenset(
    {
        Operation.DEVICES_LIST,
        Operation.DEVICE_CONFIG,
        Operation.SPEECH_CONFIG,
        Operation.ANNOUNCE,
        Operation.CONTROL,
        Operation.PLAYBACK,
        Operation.FIRMWARE_INITIATE,
        Operation.FIRMWARE_READ,
        Operation.FIRMWARE_PUBLISH,
        Operation.CHANNEL_LIST,
        Operation.CHANNEL_CONFIG,
        Operation.CREDENTIAL_CREATE,
        Operation.CREDENTIAL_DISCLOSE,
        Operation.CREDENTIAL_ROTATE,
        Operation.CREDENTIAL_REVOKE,
        Operation.SERVER_MANAGE,
        Operation.OWNER_ADMIN,
        Operation.WEBHOOK_CONFIG,
        Operation.PAIR_INITIATE,
        Operation.PAIR_APPROVE,
    }
)

# Reserved operations without a shipping handler. Enrollment v1 ships pairing separately.
UNSHIPPED = frozenset(
    {
        Operation.PLAYBACK,
        Operation.FIRMWARE_PUBLISH,
        Operation.SERVER_MANAGE,
        Operation.OWNER_ADMIN,
        Operation.CREDENTIAL_CREATE,
        Operation.CREDENTIAL_DISCLOSE,
        Operation.CREDENTIAL_ROTATE,
        Operation.CREDENTIAL_REVOKE,
    }
)


def allowed(
    principal: Principal | None,
    operation: Operation | None,
    *,
    resource: str | None = None,
    physical_verified: bool = False,
) -> bool:
    if principal is None or not isinstance(operation, Operation):
        return False
    if principal.role == Role.DEVICE:
        return operation == Operation.FIRMWARE_READ or (
            operation in _DEVICE and bool(resource) and resource == principal.subject
        )
    grants = (
        _OWNER
        if principal.role == Role.OWNER
        else (_INTEGRATION if principal.role == Role.INTEGRATION else frozenset())
    )
    return operation in grants and (
        operation not in {Operation.PAIR_INITIATE, Operation.PAIR_APPROVE} or physical_verified is True
    )


def audit_denial(authenticated: bool) -> None:
    # No client-controlled paths, IDs, tokens, payloads or exception strings.
    log.info("authorization denied: %s", "forbidden" if authenticated else "unauthorized")


@dataclass(frozen=True)
class PairApprovalResult:
    """Enrollment service returns ONLY this projection to the approver."""

    status: str
    device_id: str

    def public_dict(self) -> dict[str, str]:
        return {"status": self.status, "device_id": self.device_id}


HTTP_OPERATIONS = {
    "list_devices": Operation.DEVICES_LIST,
    "update_device": Operation.DEVICE_CONFIG,
    "announce": Operation.ANNOUNCE,
    "device_command": Operation.CONTROL,
    "list_channels": Operation.CHANNEL_LIST,
    "create_channel": Operation.CREDENTIAL_CREATE,
    "delete_channel": Operation.CREDENTIAL_REVOKE,
    "activate_channel": Operation.CHANNEL_CONFIG,
    "rotate_token": Operation.CREDENTIAL_ROTATE,
    "list_webhooks": Operation.WEBHOOK_CONFIG,
    "create_webhook": Operation.WEBHOOK_CONFIG,
    "update_webhook": Operation.WEBHOOK_CONFIG,
    "delete_webhook": Operation.WEBHOOK_CONFIG,
    "duplicate_webhook": Operation.WEBHOOK_CONFIG,
    "serve_firmware": Operation.FIRMWARE_READ,
}
WS_OPERATIONS = {
    "hello": Operation.DEVICE_CONNECT,
    "voice.start": Operation.DEVICE_AUDIO,
    "voice.end": Operation.DEVICE_AUDIO,
    "abort": Operation.DEVICE_CONTROL,
    "realtime.start": Operation.DEVICE_AUDIO,
    "realtime.media_ready": Operation.DEVICE_CONTROL,
    "realtime.pause": Operation.DEVICE_CONTROL,
    "realtime.resume": Operation.DEVICE_CONTROL,
    "realtime.stop": Operation.DEVICE_CONTROL,
    "device.button": Operation.DEVICE_BUTTON,
}
