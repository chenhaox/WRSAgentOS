"""Microphone recording and scripted capture, without model dependencies.

record_* returns PCM audio; make_mock_capture returns scripted text directly,
matching the capture callback used by AsrNode after recognition.
"""

import os
import time

# A lost button release must not record forever. Overlong input is discarded whole.
CAPTURE_SECONDS = 15.0


def record_command(*, threshold=0.015, silence_seconds=0.25, max_seconds=3.0):
    """Explicit push-to-talk session, ending after speech + silence, with no queued recordings.

    Blocking device reads belong in asyncio.to_thread(). RMS segmentation is not intent/VAD
    authority: only a complete recognized command can reach Voice. Overlong/overflowed input
    is discarded as a whole, not truncated into a potentially different command.
    """
    if not 0 < threshold < 1 or not 0 < silence_seconds < max_seconds <= 4:
        raise ValueError("invalid_microphone_segmentation_settings")
    import numpy as np
    import sounddevice as sd

    rate, block = 16000, 320
    heard, quiet, waiting = False, 0, 0
    chunks, preroll = [], []
    with sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=block) as stream:
        while True:
            audio, overflowed = stream.read(block)
            if overflowed:
                raise RuntimeError("microphone_overflow: utterance_discarded")
            loud = float(np.sqrt(np.mean(audio**2))) >= threshold
            if not heard:
                preroll = [*preroll[-4:], audio.copy()]
                waiting += block
                if not loud:
                    if waiting >= rate * 5:
                        return None
                    continue
                heard = True
                chunks.extend(preroll)
            else:
                chunks.append(audio.copy())
            quiet = 0 if loud else quiet + block
            if len(chunks) * block >= rate * max_seconds:
                return None
            if quiet >= rate * silence_seconds:
                return np.concatenate(chunks).reshape(-1)


def _resample(audio, from_rate, to_rate):
    import numpy as np

    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if from_rate == to_rate or audio.size == 0:
        return audio
    count = int(round(audio.size * to_rate / from_rate))
    if count < 1:
        return audio[:0]
    position = np.linspace(0, audio.size - 1, count)
    left = np.floor(position).astype(np.int64)
    mix = (position - left).astype(np.float32)
    right = np.minimum(left + 1, audio.size - 1)
    return (audio[left] * (1 - mix) + audio[right] * mix).astype(np.float32)


def choose_input_device():
    """Pick the capture device that is actually receiving sound.

    WRS_AGENT_AUDIO_INPUT overrides the probe. Otherwise each input device is sampled
    for 0.2 seconds and the one with the highest RMS is used.
    """
    chosen = os.environ.get("WRS_AGENT_AUDIO_INPUT", "").strip()
    if chosen:
        return chosen
    import numpy as np
    import sounddevice as sd

    best, best_rms = None, -1.0
    for index, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] < 1:
            continue
        rate = int(info["default_samplerate"] or 48000)
        try:
            frames = sd.rec(
                int(rate * 0.2), samplerate=rate, channels=1, dtype="float32", device=index,
            )
            sd.wait()
        except Exception:
            continue
        samples = np.asarray(frames, dtype=np.float32)
        rms = float(np.sqrt(np.mean(samples * samples)))
        if rms > best_rms:
            best, best_rms = index, rms
    return best


def _capture_rate(device, preferred=16000):
    import sounddevice as sd

    info = sd.query_devices(device, "input") if device is not None else sd.query_devices(kind="input")
    native = int(info["default_samplerate"] or preferred)
    for rate in (preferred, native):
        try:
            sd.check_input_settings(device=device, channels=1, dtype="float32", samplerate=rate)
            return rate
        except Exception:
            continue
    return native


def record_push_to_talk(held, *, max_seconds=CAPTURE_SECONDS, min_seconds=0.2, device=None):
    """Record while the caller keeps the button down; the caller owns segmentation.

    Blocking device reads belong in asyncio.to_thread(). Releasing is the only normal end;
    max_seconds bounds a release signal that never arrives. An overlong, overflowed or
    accidental short press is discarded whole, never truncated into a different command.
    Audio is returned at 16 kHz even when the device only opens at its native rate.
    """
    if not 0 < min_seconds < max_seconds <= CAPTURE_SECONDS:
        raise ValueError("invalid_microphone_capture_settings")
    import numpy as np
    import sounddevice as sd

    target = 16000
    rate = _capture_rate(device, target)
    block = max(1, int(rate * 0.02))
    chunks = []
    with sd.InputStream(
        device=device, samplerate=rate, channels=1, dtype="float32", blocksize=block,
    ) as stream:
        while held():
            audio, overflowed = stream.read(block)
            if overflowed:
                raise RuntimeError("microphone_overflow: utterance_discarded")
            chunks.append(audio.copy())
            if len(chunks) * block >= rate * max_seconds:
                return None
    if len(chunks) * block < rate * min_seconds:
        return None
    return _resample(np.concatenate(chunks), rate, target)


def make_mock_capture(script=(), *, max_seconds=CAPTURE_SECONDS):
    """Offline fixture: hold for as long as the caller presses, then return scripted text."""
    pending = list(script)

    def capture(held):
        # One press consumes one scripted line, so a discarded press cannot shift the rest.
        deadline, text = time.monotonic() + max_seconds, pending.pop(0) if pending else None
        while held():
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.005)
        return text

    return capture
