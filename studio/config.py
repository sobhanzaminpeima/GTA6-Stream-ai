"""Settings, read from the project .env plus environment variables.

Every external capability is optional: when a key is missing the connector reports
NOT CONFIGURED in the status dashboard instead of failing silently or faking output.
"""
from __future__ import annotations

import os
import pathlib
import sys
from dataclasses import dataclass, field

FROZEN = getattr(sys, "frozen", False)  # running from the packaged .exe
ROOT = pathlib.Path(__file__).resolve().parents[1]
if FROZEN:  # installed app: everything the user owns lives in %LOCALAPPDATA%\GTA6StreamAI
    APP_HOME = pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home())) / "GTA6StreamAI"
    DATA = APP_HOME / "data"
    ENV_FILE = APP_HOME / ".env"
else:
    APP_HOME = ROOT
    DATA = ROOT / "studio_data"
    ENV_FILE = ROOT / ".env"

ENV_KEYS = ["YOUTUBE_API_KEY", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "TWITCH_USER_TOKEN", "TWITCH_USER_ID",
            "ELEVENLABS_API_KEY", "ANTHROPIC_API_KEY", "STUDIO_CHANNEL_HANDLE", "STUDIO_CHANNEL_NAME"]


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ENV_FILE
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("STUDIO_", "YOUTUBE_", "TWITCH_", "ELEVENLABS_", "ANTHROPIC_"))})
    return env


@dataclass
class Settings:
    env: dict[str, str] = field(default_factory=_load_env)

    def get(self, key: str, default: str = "") -> str:
        return self.env.get(key, default)

    # storage
    @property
    def data_dir(self) -> pathlib.Path:
        d = pathlib.Path(self.get("STUDIO_DATA_DIR", str(DATA)))
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def database_url(self) -> str:
        # SQLite by default (single-machine creator setup). Any SQLAlchemy URL works,
        # e.g. postgresql+psycopg://user:pass@host/studio for a server deployment.
        return self.get("STUDIO_DATABASE_URL", f"sqlite:///{(self.data_dir / 'studio.db').as_posix()}")

    # channel (override with STUDIO_CHANNEL_HANDLE / STUDIO_CHANNEL_NAME)
    @property
    def channel_handle(self) -> str:
        return self.get("STUDIO_CHANNEL_HANDLE", "@gtavi_rockstar_v")

    @property
    def channel_name(self) -> str:
        return self.get("STUDIO_CHANNEL_NAME", "GTA VI Central")

    def reload(self) -> None:
        keep = {k: v for k, v in self.env.items() if k.startswith("STUDIO_DATA") or k == "STUDIO_DATABASE_URL"}
        self.env = _load_env()
        self.env.update({k: v for k, v in keep.items() if k not in self.env})

    def save_env(self, values: dict[str, str]) -> None:
        """Write keys to the local .env (never sent anywhere; values are not returned by the API)."""
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
        for k, v in values.items():
            if k not in ENV_KEYS:
                raise ValueError(f"unknown key {k}")
            v = v.strip().replace("\n", "")
            lines = [ln for ln in lines if not ln.split("=", 1)[0].strip() == k]
            if v:
                lines.append(f"{k}={v}")
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.reload()

    # providers
    @property
    def elevenlabs_key(self) -> str:
        return self.get("ELEVENLABS_API_KEY")

    elevenlabs_voice: str = "nPczCjzI2devNBz1zQrb"  # "Brian", the channel narrator (RULES.md)
    elevenlabs_model: str = "eleven_multilingual_v2"

    @property
    def anthropic_key(self) -> str:
        return self.get("ANTHROPIC_API_KEY")

    @property
    def llm_model(self) -> str:
        return self.get("STUDIO_LLM_MODEL", "claude-opus-5-5")

    @property
    def youtube_api_key(self) -> str:
        return self.get("YOUTUBE_API_KEY")

    @property
    def youtube_client_secrets(self) -> pathlib.Path:
        return pathlib.Path(self.get("YOUTUBE_CLIENT_SECRETS", str(APP_HOME / "client_secret.json")))

    @property
    def twitch_client_id(self) -> str:
        return self.get("TWITCH_CLIENT_ID")

    @property
    def twitch_client_secret(self) -> str:
        return self.get("TWITCH_CLIENT_SECRET")

    @property
    def secret_key(self) -> bytes:
        """Fernet key for credential encryption at rest. Generated once, kept in studio_data."""
        from cryptography.fernet import Fernet
        k = self.get("STUDIO_SECRET_KEY")
        if k:
            return k.encode()
        p = self.data_dir / ".secret_key"
        if not p.exists():
            p.write_bytes(Fernet.generate_key())
        return p.read_bytes().strip()

    # privacy
    chat_retention_days: int = 7


settings = Settings()
