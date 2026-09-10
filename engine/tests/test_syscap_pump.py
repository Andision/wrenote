"""Recording the meeting and not yourself.

The whole live pipeline used to be clocked by the microphone: `mix(mic_pcm)`
ran once per arriving mic frame, so "system audio only" was not a checkbox
away — with no mic there were no frames and nothing advanced. The pump is
the missing clock.

What these hold: it runs at real time, it pads rather than stalls when the
source is momentarily behind, pause stops it, and stop is clean.
"""
from __future__ import annotations

import asyncio

import pytest

import wrenote.core.syscap as syscap
from wrenote.core.syscap import FRAME_BYTES, SystemAudioPump

pytestmark = pytest.mark.anyio


class FakeSource:
    """A SystemAudioSource stand-in: whatever has been fed, popped in order."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.started = False
        self.stopped = False

    def push(self, data: bytes) -> None:
        self.buf.extend(data)

    def read(self, n: int) -> bytes:
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    async def start(self) -> bool:
        self.started = True
        return True

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def source(monkeypatch):
    src = FakeSource()
    monkeypatch.setattr(syscap, "make_system_audio_source", lambda app=None: src)
    return src


async def _pump(frames: list[bytes]) -> SystemAudioPump:
    async def on_frame(f: bytes) -> None:
        frames.append(f)
    return SystemAudioPump(on_frame)


class TestPump:
    async def test_feeds_whole_frames_at_a_time(self, source):
        frames: list[bytes] = []
        pump = await _pump(frames)
        source.push(b"\x01\x02" * (FRAME_BYTES // 2) * 3)
        assert await pump.start()
        await asyncio.sleep(syscap.FRAME_MS / 1000 * 3.5)
        await pump.stop()
        assert frames, "the pump produced nothing"
        assert all(len(f) == FRAME_BYTES for f in frames)
        assert frames[0] == b"\x01\x02" * (FRAME_BYTES // 2)

    async def test_pads_with_silence_rather_than_stalling(self, source):
        """A source that falls behind must not stop the clock: a gap in the
        audio is silence, and the transcript's timeline has to say so."""
        frames: list[bytes] = []
        pump = await _pump(frames)
        source.push(b"\x7f\x7f" * (FRAME_BYTES // 4))  # half a frame
        assert await pump.start()
        await asyncio.sleep(syscap.FRAME_MS / 1000 * 2.5)
        await pump.stop()
        assert len(frames) >= 2
        assert len(frames[0]) == FRAME_BYTES
        assert frames[0].endswith(bytes(FRAME_BYTES // 2))  # padded
        assert frames[1] == bytes(FRAME_BYTES)  # nothing left: pure silence

    async def test_pause_stops_feeding_and_resume_starts_again(self, source):
        frames: list[bytes] = []
        pump = await _pump(frames)
        assert await pump.start()
        await asyncio.sleep(syscap.FRAME_MS / 1000 * 1.5)
        pump.set_paused(True)
        n = len(frames)
        await asyncio.sleep(syscap.FRAME_MS / 1000 * 3)
        assert len(frames) == n, "paused pump kept feeding"
        pump.set_paused(False)
        await asyncio.sleep(syscap.FRAME_MS / 1000 * 2)
        assert len(frames) > n
        await pump.stop()

    async def test_stop_releases_the_source_and_the_task(self, source):
        frames: list[bytes] = []
        pump = await _pump(frames)
        assert await pump.start()
        await pump.stop()
        assert source.stopped
        n = len(frames)
        await asyncio.sleep(syscap.FRAME_MS / 1000 * 2)
        assert len(frames) == n, "the pump task outlived stop()"

    async def test_no_source_means_no_pump(self, monkeypatch):
        monkeypatch.setattr(syscap, "make_system_audio_source", lambda app=None: None)
        pump = await _pump([])
        assert await pump.start() is False
        await pump.stop()  # must not raise


class TestAppScope:
    """"Record Zoom, not the browser." The platform is asked for one app's
    audio; what it can't scope, it must not silently widen."""

    async def test_the_app_reaches_the_platform(self, monkeypatch):
        seen: list[str | None] = []

        def fake(app=None):
            seen.append(app)
            return FakeSource()

        monkeypatch.setattr(syscap, "make_system_audio_source", fake)
        syscap.SystemAudioMixer("us.zoom.xos")
        SystemAudioPump(lambda _f: asyncio.sleep(0), "us.zoom.xos")
        assert seen == ["us.zoom.xos", "us.zoom.xos"]

    async def test_no_app_still_means_everything(self, monkeypatch):
        seen: list[str | None] = []
        monkeypatch.setattr(
            syscap, "make_system_audio_source", lambda app=None: (seen.append(app), FakeSource())[1]
        )
        syscap.SystemAudioMixer()
        assert seen == [None]

    def test_a_platform_that_cannot_scope_says_so(self):
        """The client asks before offering the choice: capturing the whole
        desktop when someone asked for one app records more than they agreed
        to, so 'can't' has to be visible rather than silently approximated."""
        from wrenote.platform.base import PlatformAdapter

        assert PlatformAdapter.system_audio_can_scope.fget(object()) is False  # type: ignore[attr-defined]
