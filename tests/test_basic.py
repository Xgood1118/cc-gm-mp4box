# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mp4box.core import Box, find, parse_boxes  # noqa: E402


def test_box_repr():
    b = Box("ftyp", 0, 24, 8, 16)
    assert "ftyp" in repr(b)


def test_parse_top_level():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "data", "clip.mp4")
    if not os.path.exists(path):
        return
    with open(path, "rb") as f:
        data = f.read()
    boxes = parse_boxes(data, 0, len(data))
    tags = [b.tag for b in boxes]
    assert "ftyp" in tags
    assert "moov" in tags
    assert tags[-1] == "moov"


def test_find():
    boxes = [Box("a", 0, 8, 8, 0), Box("moov", 8, 8, 16, 0)]
    assert find(boxes, "moov") is not None
    assert find(boxes, "trak") is None
