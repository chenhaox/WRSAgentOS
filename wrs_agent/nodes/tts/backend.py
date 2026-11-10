"""Speech execution: output state, local playback, and real/offline action backends."""

import asyncio
import os
import threading
import uuid
import wave
from dataclasses import dataclass, replace
from pathlib import Path

from wrs_agent.executor import ActionExecutor, ExecutionUnknown, SkillFailure
from wrs_agent.schemas import SpeechData
from wrs_agent.skills.speech import SKILLS, SpeakArgs


@dataclass
class SpeechState:
    """Snapshot data shared by real and offline speech execution."""

    version: int = 0
    completed: int = 0
    last_text: str = ""

    def snapshot(self):
        return SpeechData(completed=self.completed, last_text=self.last_text)


def mirror_playback(audio, rate):
    """Copy one utterance for the remote page. Local playback still owns success."""
    directory = os.environ.get("WRS_SPEECH_MIRROR", "").strip()
    if not directory:
        return
    import numpy as np

    samples = np.clip(np.asarray(audio, dtype="float32").reshape(-1), -1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2")
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    speech_id = uuid.uuid4().hex
    temporary = folder / f".speech-{speech_id}.wav"
    with wave.open(str(temporary), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(rate))
        handle.writeframes(pcm.tobytes())
    temporary.replace(folder / "speech.wav")
    (folder / "speech.id").write_text(speech_id)


def play_audio(chunks, rate, stopped):
    """Only the owner thread touches the stream; cancel aborts queued local output."""
    import numpy as np
    import sounddevice as sd

    # Synthesis streams in chunks so that stop can land between them; join them here.
    audio = np.concatenate(
        [np.asarray(chunk, dtype="float32").reshape(-1) for chunk in chunks]
    ).reshape(-1, 1)
    if not len(audio) or not np.isfinite(audio).all():
        raise SkillFailure("invalid_synthesized_audio")
    try:
        mirror_playback(audio, rate)
    except Exception:
        pass  # The robot speaker is the confirmed output; the remote copy is extra.
    stream = None
    try:
        if stopped.is_set():
            return False
        # "low" underruns on this machine. A recovered underrun still makes
        # write() return a count; that used to abort the rest of the utterance.
        # This machine's default ALSA device rejects the synthesizer rate.
        # WRS_AUDIO_DEVICE selects one that can play it (with PA_ALSA_PLUGHW).
        chosen = os.environ.get("WRS_AUDIO_DEVICE", "").strip()
        stream = sd.OutputStream(
            samplerate=rate, channels=1, dtype="float32", latency="high",
            device=int(chosen) if chosen.isdigit() else (chosen or None),
        )
        stream.start()
        block = max(1, rate // 25)
        for offset in range(0, len(audio), block):
            if stopped.is_set():
                stream.abort()
                return False
            stream.write(audio[offset : offset + block])
        if stopped.is_set():
            stream.abort()
            return False
        stream.stop()  # Drain the final buffer before reporting completion.
        return not stopped.is_set()
    except Exception as exc:
        if stream is not None:
            try:
                stream.abort()
            except Exception as abort_error:
                raise ExecutionUnknown("audio_abort_not_confirmed") from abort_error
        raise ExecutionUnknown("audio_output_not_confirmed") from exc
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception as close_error:
                raise ExecutionUnknown("audio_close_not_confirmed") from close_error


class SpeechBackend:
    """Compose blocking render(text, stopped) and play(chunks, rate, stopped) functions.

    One ActionExecutor owns this backend. Synthesis and playback run in worker
    threads; cancellation waits for the active worker to confirm it has stopped.
    """

    def __init__(self, render, play=play_audio):
        self.render, self.play = render, play
        self.cache = {}

    def prepare(self, texts):
        if isinstance(texts, str) or len(set(self.cache).union(texts)) > 32:
            raise ValueError("too_many_prepared_utterances")
        for text in texts:
            SpeakArgs(text=text)
            audio = self.render(text, threading.Event())
            if audio is None:
                raise RuntimeError("speech_preparation_failed")
            self.cache[text] = audio
        # Only explicitly prepared fixed phrases are cached, never arbitrary user transcripts.

    async def speak(self, state, args, stop, progress):
        stopped = threading.Event()
        if stop.is_set():
            return False

        def synthesize():
            try:
                audio = self.cache.get(args.text)
                if audio is None:
                    audio = self.render(args.text, stopped)
            except Exception as exc:
                raise SkillFailure("speech_synthesis_failed") from exc
            return audio

        async def relay_stop():
            await stop.wait()
            stopped.set()

        relay = asyncio.create_task(relay_stop())
        worker = asyncio.create_task(asyncio.to_thread(synthesize))
        try:
            audio = await asyncio.shield(worker)
            # The control loop owns stop. Check it here before scheduling playback,
            # even when the relay coroutine has not yet notified the worker thread.
            if stop.is_set():
                stopped.set()
            if stopped.is_set() or audio is None:
                return False
            worker = asyncio.create_task(asyncio.to_thread(self.play, *audio, stopped))
            completed = await asyncio.shield(worker)
        except asyncio.CancelledError:
            stopped.set()
            # A cancelled coroutine is not proof of stopped playback.
            await asyncio.shield(worker)
            raise
        finally:
            relay.cancel()
            await asyncio.gather(relay, return_exceptions=True)
        if completed and not stop.is_set():
            state.completed += 1
            state.last_text = args.text
            progress(1.0)
            return True
        return False


def make_speech_executor(journal, backend):
    """Bind synthesis/playback to the shared speak action and its stop contract."""
    contract = SKILLS["speak"]
    skill = replace(
        contract, verification="local_audio_output_drained", handler=backend.speak,
    )
    return ActionExecutor(
        journal,
        state=SpeechState(),
        skills=[skill],
        backend="qwen_tts",
        features_extra={
            "robot_controls": False,
            "controller_flush": False,
            "controlled_stop": True,
            "verification": "local_audio_output_drained",
            "stop_scope": "local_audio_stream_abort",
        },
    )


def make_mock_tts(journal_path, *, duration=0.4):
    """Simulate speech completion for offline runs; never open an audio device."""
    async def speak(state, args, stop, progress):
        if duration > 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=duration)
            except TimeoutError:
                pass
        if stop.is_set():
            return False
        # Virtual completion only; no claim that a device produced audible output.
        state.last_text = args.text
        state.completed += 1
        return state.last_text == args.text

    return ActionExecutor(
        journal_path,
        state=SpeechState(),
        skills=[SKILLS["speak"].bind(speak)],
        backend="mock_tts",
        features_extra={
            "robot_controls": False,
            "controller_flush": False,
            "controlled_stop": False,
        },
    )
