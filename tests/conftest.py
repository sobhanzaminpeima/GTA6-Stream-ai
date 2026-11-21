import os
import pathlib
import shutil
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="studio_test_"))
os.environ["STUDIO_DATA_DIR"] = str(_TMP)
os.environ["STUDIO_NO_WORKERS"] = "1"

from studio.config import settings  # noqa: E402

settings.env["STUDIO_DATA_DIR"] = str(_TMP)
settings.env["STUDIO_NO_WORKERS"] = "1"
for k in ("ANTHROPIC_API_KEY", "YOUTUBE_API_KEY", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"):
    settings.env.pop(k, None)
settings.env["STUDIO_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"

from studio.db import migrate  # noqa: E402

migrate()


@pytest.fixture(scope="session")
def tmpdir_session():
    return _TMP


@pytest.fixture(scope="session")
def sample_video(tmpdir_session):
    """Synthetic, self-made test video: color bars + tone, a hard cut and a loud burst at 8 s."""
    from studio.media import run
    out = tmpdir_session / "sample.mp4"
    if not out.exists():
        run(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8", "-f", "lavfi", "-i",
             "smptehdbars=size=1280x720:rate=30:duration=10",
             "-f", "lavfi", "-i", "sine=frequency=330:duration=8:sample_rate=48000", "-f", "lavfi", "-i",
             "sine=frequency=660:duration=10:sample_rate=48000",
             "-filter_complex", "[2:a]volume=0.05[a0];[3:a]volume=0.9[a1];[0:v][a0][1:v][a1]concat=n=2:v=1:a=1[v][a]",
             "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out)])
    return out


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("STUDIO_KEEP_TEST") != "1":
        shutil.rmtree(_TMP, ignore_errors=True)
    else:
        print(f"\nkept test data in {_TMP}")
