import asyncio
import hashlib
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

from wrs_agent.nodes.vision.node import VisionState
from wrs_agent.nodes.vision.results import Object2D, Objects2D
from wrs_agent.nodes.vision.yolo import YoloDetector
from wrs_agent.skills.vision import DescribeArgs, describe_objects, describe_result


def result(**updates):
    values = dict(source="replay", result_id="result", observation_id="session.1",
                  source_stream_id="session", captured_at_ns=1, width=320, height=240,
                  model="fixture", weights_sha256="0" * 64, items=[])
    return Objects2D(**(values | updates))


def test_no_detections_is_not_a_claim_of_an_empty_table():
    assert describe_result(result()) == "画面中未识别到物体。"
    assert describe_result(result(scope="table_region")) == "桌面区域未识别到物体。"


def test_boxes_have_finite_ordered_bounds():
    for box in ([1., 0., 0., 2.], [0., 0., float("nan"), 2.]):
        with pytest.raises(ValueError):
            Object2D(instance_id="a", label="cup", score=0.5, box=box)
    item = Object2D(instance_id="a", label="cup", score=0.5, box=[0., 0., 321., 20.])
    with pytest.raises(ValueError):
        result(items=[item])


async def test_description_retains_selected_result_and_cancellation_rejects_stale_input():
    state = VisionState(latest=result(), observed_at=time.monotonic(), health="ready")
    stop = asyncio.Event()
    output = await describe_objects(state, DescribeArgs(), stop, lambda _: None)
    state.latest = result(result_id="new")
    assert output.result_id == "result"
    state.observed_at -= 20
    pending = asyncio.create_task(describe_objects(state, DescribeArgs(), stop, lambda _: None))
    await asyncio.sleep(0.06)
    assert not pending.done()
    stop.set()
    assert await pending is False


def test_yolo_requires_local_checkpoint_and_verified_bytes(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "ultralytics", None)
    with pytest.raises(FileNotFoundError):
        YoloDetector(tmp_path / "missing.pt", "0" * 64)
    path = tmp_path / "test.pt"
    path.write_bytes(b"fixture-only")
    with pytest.raises(ValueError, match="checksum"):
        YoloDetector(path, "0" * 64)


def test_yolo_adapter_passes_bgr_and_normalizes_model_output(tmp_path, monkeypatch):
    class Tensor:
        def __init__(self, data):
            self.data = np.array(data)

        def cpu(self):
            return self

        def numpy(self):
            return self.data

    class Model:
        names = {0: "cup"}

        def __init__(self, path, task):
            assert task == "detect" and path.endswith("test.pt")

        def predict(self, bgr, **options):
            assert bgr[0, 0].tolist() == [30, 20, 10] and bgr.flags.c_contiguous
            assert options["max_det"] == 32 and options["save"] is False
            boxes = SimpleNamespace(xyxy=Tensor([[1, 2, 3, 4]]),
                                    conf=Tensor([0.9]), cls=Tensor([0]))
            return [SimpleNamespace(boxes=boxes, names={0: "cup"})]

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(
        YOLO=Model, YOLOWorld=Model, settings=SimpleNamespace(update=lambda _: None),
    ))
    path = tmp_path / "test.pt"
    path.write_bytes(b"fixture-only")
    detector = YoloDetector(path, hashlib.sha256(path.read_bytes()).hexdigest())
    rgb = np.full((8, 8, 3), [10, 20, 30], dtype=np.uint8)
    items = detector.detect(rgb)
    assert items[0].label == "cup" and items[0].box == [1., 2., 3., 4.]
    with pytest.raises(ValueError, match="unsupported_target_label"):
        detector.locate(rgb, "red cup")


def test_world_detector_restores_announcement_vocabulary_after_failure():
    model = SimpleNamespace(txt_feats="original", names={0: "cup"},
                            model=[SimpleNamespace(nc=1)],
                            clip_model=SimpleNamespace(tokenize=lambda *a, **k: None))

    def set_classes(target):
        model.txt_feats, model.names, model.model[-1].nc = "temporary", target, len(target)

    detector = object.__new__(YoloDetector)
    detector.model = SimpleNamespace(model=model, set_classes=set_classes, predictor="old")
    detector.text_sha256, detector.labels = "1" * 64, ["cup"]
    detector.default_embeddings = "original"

    def fail(_):
        raise RuntimeError("inference_failed")

    detector.detect = fail
    with pytest.raises(RuntimeError, match="inference_failed"):
        detector.locate(None, "red cup")
    assert model.txt_feats == "original" and model.names == {0: "cup"} and model.model[-1].nc == 1
    assert detector.model.predictor is None
