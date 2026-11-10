"""Resident, offline Qwen recognition; compose it with local microphone capture."""

import os
import sys
import time
from dataclasses import dataclass
from importlib.util import find_spec

from wrs_agent.nodes.asr.capture import CAPTURE_SECONDS, choose_input_device, record_push_to_talk
from wrs_agent.nodes.model_assets import ROOT, model_directory, offline_cuda


@dataclass(frozen=True)
class Transcript:
    text: str
    inference_seconds: float


class QwenASR:
    def __init__(self, *, vocabulary=()):
        if isinstance(vocabulary, str) or len(vocabulary) > 64:
            raise ValueError("asr_vocabulary_requires_at_most_64_words")
        if any(not isinstance(word, str) or not 0 < len(word) <= 32 for word in vocabulary):
            raise ValueError("invalid_asr_vocabulary_word")
        self.vocabulary = "、".join(vocabulary)
        if find_spec("qwen_asr") is None:
            environment = ROOT / ".local/venvs/qwen-asr"
            interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            raise RuntimeError(
                f"qwen_asr is not installed in {sys.executable}. "
                f"Run this example with {interpreter}; "
                "prepare that environment with scripts/setup_speech.ps1 if missing."
            )
        directory = model_directory("asr")
        torch = offline_cuda()
        from qwen_asr import Qwen3ASRModel

        self.model = Qwen3ASRModel.from_pretrained(
            str(directory),
            device_map="cuda:0",
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
            local_files_only=True,
            max_inference_batch_size=1,
            max_new_tokens=256,
        )

    def transcribe(self, audio, sample_rate=16000):
        import numpy as np

        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim != 1 or not 0 < len(audio) <= sample_rate * CAPTURE_SECONDS:
            raise ValueError("asr_requires_mono_command_within_capture_limit")
        if sample_rate != 16000 or not np.isfinite(audio).all():
            raise ValueError("asr_requires_finite_16khz_audio")
        started = time.perf_counter()
        results = self.model.transcribe(
            audio=(audio, sample_rate),
            language="Chinese",
            context="机械臂控制指令：" + self.vocabulary + "。" if self.vocabulary else "",
        )
        return Transcript(results[0].text.strip(), time.perf_counter() - started)

    def warmup(self):
        import numpy as np

        self.transcribe(np.zeros(8000, dtype=np.float32))


def make_qwen_capture(*, vocabulary=(), max_seconds=CAPTURE_SECONDS):
    """Load the resident recognizer once, then capture and transcribe one press at a time."""
    recognizer = QwenASR(vocabulary=vocabulary)
    recognizer.warmup()
    device = choose_input_device()
    import sounddevice as sd

    name = sd.query_devices(device, "input")["name"] if device is not None else "default"
    print(f"asr input: {name}", flush=True)

    def capture(held):
        audio = record_push_to_talk(held, max_seconds=max_seconds, device=device)
        return None if audio is None else recognizer.transcribe(audio).text.strip() or None

    return capture
