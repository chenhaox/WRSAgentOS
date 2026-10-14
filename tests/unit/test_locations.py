import json
import struct

import numpy as np
import pytest

from tests.fixtures.grasping.virtual_data import data
from wrs_agent.nodes.segmentation.data import SegmentInfo
from wrs_agent.nodes.vision.locations import (
    LocationData,
    LocationInfo,
    pack_location,
    select_object,
    unpack_location,
)
from wrs_agent.nodes.vision.results import Object2D
from wrs_agent.nodes.vision.selection import select_box_mask


@pytest.fixture
def located():
    segment = data().segment
    info = LocationInfo(location_id="located", owner=segment.info.owner,
                        observation=segment.info.observation, target="box",
                        object=Object2D(instance_id="0", label="box", score=.9,
                                        box=[12., 8., 20., 16.]),
                        model="fixture", weights_sha256="0" * 64)
    return LocationData(info, segment.arrays)


@pytest.mark.parametrize("alignment", ["depth_grid", "color_grid"])
def test_location_packet_retains_complete_readonly_observation(located, alignment):
    observation = located.info.observation.model_copy(update={
        "alignment": alignment, "frame_id": "fixture-" + alignment,
    })
    located = LocationData(located.info.model_copy(update={"observation": observation}),
                           located.arrays)
    restored, age = unpack_location(pack_location(located, .5))
    assert restored.info == located.info and age == .5
    for name in ("rgb", "depth", "points"):
        np.testing.assert_array_equal(restored.arrays[name], located.arrays[name])
        assert not restored.arrays[name].flags.writeable


@pytest.mark.parametrize("change", [lambda p: p[:3], lambda p: p[:-1], lambda p: p + b"x"])
def test_location_rejects_malformed_packet(located, change):
    with pytest.raises(ValueError):
        unpack_location(change(pack_location(located, 0)))


def test_location_rejects_mismatched_embedded_frame(located):
    packet = pack_location(located, 0)
    size, = struct.unpack_from("<I", packet)
    header = json.loads(packet[4:4 + size])
    header["observation"]["observation_id"] = "another.frame"
    header = json.dumps(header).encode()
    with pytest.raises(ValueError, match="observation_mismatch"):
        unpack_location(struct.pack("<I", len(header)) + header + packet[4 + size:])


def test_location_checks_box_target_and_text_provenance(located):
    payload = located.info.model_dump()
    for changed in ({"target": "cup"}, {"text_weights_sha256": "1" * 64},
                    {"object": payload["object"] | {"box": [1., 1., 40., 20.]}}):
        with pytest.raises(ValueError):
            LocationInfo.model_validate(payload | changed)
    with pytest.raises(ValueError, match="complete_observation"):
        LocationData(located.info, {"rgb": located.arrays["rgb"]})


def test_object_selection_preserves_attributes_and_rejects_multiple_matches(located):
    first = located.info.object
    second = first.model_copy(update={"instance_id": "1", "box": [22., 8., 30., 16.]})
    assert select_object([first, second], "box", "rightmost") == second
    with pytest.raises(ValueError, match="target_ambiguous"):
        select_object([first, second], "box", "only")
    with pytest.raises(ValueError, match="target_not_found"):
        select_object([first], "red box", "only")


def test_box_prompt_requires_a_unique_well_overlapping_mask():
    masks = np.zeros((2, 20, 30), dtype=bool)
    masks[0, 2:10, 2:10] = True
    masks[1, 2:10, 20:28] = True
    assert select_box_mask(masks, [2, 2, 10, 10]) == (0, 1.)
    with pytest.raises(ValueError, match="region_mask_not_found"):
        select_box_mask(masks, [12, 12, 18, 18])
    with pytest.raises(ValueError, match="invalid_region_box"):
        select_box_mask(masks, [0, 0, 31, 20])
    masks[1] = masks[0]
    with pytest.raises(ValueError, match="region_mask_ambiguous"):
        select_box_mask(masks, [2, 2, 10, 10])


def test_segment_location_must_match_task_observation_and_target(located):
    segment = data().segment.info.model_dump()
    segment.update(location=located.info.model_dump(), score_kind="box_iou")
    # Use the exact retained observation; separate fixture calls have different timestamps.
    segment["observation"] = located.info.observation.model_dump()
    assert SegmentInfo.model_validate(segment).location.location_id == "located"
    for changed in ({"target": "cup"}, {"observation": segment["observation"] |
                                       {"observation_id": "another"}},
                    {"owner": segment["owner"] | {"task_id": "another"}}, {"location": None}):
        with pytest.raises(ValueError):
            SegmentInfo.model_validate(segment | changed)


def test_box_prompt_accepts_thin_shape_and_rejects_a_merged_neighbor():
    masks = np.zeros((3, 20, 30), dtype=bool)
    # A bent object fills less than half its enclosing rectangle.
    masks[0, 2:10, 2:4] = True
    masks[0, 8:10, 2:10] = True
    masks[1] = masks[0]
    masks[1, 2:10, 15:20] = True
    assert select_box_mask(masks, [2, 2, 10, 10]) == (0, 1.)
    # Empty masks cannot compete, even when their accidental bounds match the whole frame.
    with pytest.raises(ValueError, match="region_mask_not_found"):
        select_box_mask(masks[2:], [0, 0, 30, 20])


def test_box_prompt_uses_half_open_bounds_for_edge_pixels():
    masks = np.zeros((1, 20, 30), dtype=bool)
    masks[0, -1, -1] = True
    assert select_box_mask(masks, [29, 19, 30, 20]) == (0, 1.)
