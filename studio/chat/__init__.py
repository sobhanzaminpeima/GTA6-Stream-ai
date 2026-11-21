"""Chat ingestion: normalization, platform connectors (YouTube, Twitch) and a simulator."""
from dataclasses import dataclass


@dataclass
class RawMessage:
    platform: str
    msg_id: str
    ts: float           # unix seconds, platform publish time when available
    user_id: str        # hashed immediately on ingest, never stored raw
    text: str
    is_bot: bool = False
    kind: str = "message"  # message | raid | giveaway | mod | system
