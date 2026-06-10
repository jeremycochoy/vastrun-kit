"""Tests for `vastrun_kit.config` default-image invariants (issue #28).

A fresh `vastrun-provision` instance must be training-ready out of the box:
`python3 -c "import torch"` works with no pip bootstrap. That holds only if
the default images are PyTorch runtime images whose torch satisfies the floor
the training repos pin (`torch>=2.9.1` in rnd/requirements.txt) and whose
CUDA build carries kernels for the GPU arch the image serves.

Covered:
- both defaults are official `pytorch/pytorch` `*-runtime` images
  (torch + pip preinstalled, no PEP 668 fight);
- both ship torch >= 2.9.1;
- both are cu12.8+ builds — the first CUDA with Blackwell sm_120/sm_100
  kernels — so a Blackwell name missing from BLACKWELL_GPU_PREFIXES still
  falls through to a default image that works on it.
"""

from __future__ import annotations

import re

import pytest

from vastrun_kit import config

_DEFAULT_IMAGES = (config.IMAGE, config.BLACKWELL_IMAGE)


def _torch_version(image: str) -> tuple[int, ...]:
    m = re.match(r"(\d+(?:\.\d+)+)-cuda", image.split(":", 1)[1])
    assert m, f"tag of {image!r} does not start with a torch version"
    return tuple(int(p) for p in m.group(1).split("."))


def _cuda_version(image: str) -> tuple[int, ...]:
    m = re.search(r"-cuda(\d+(?:\.\d+)+)-", image)
    assert m, f"{image!r} has no -cudaX.Y- component"
    return tuple(int(p) for p in m.group(1).split("."))


@pytest.mark.parametrize("image", _DEFAULT_IMAGES)
def test_default_images_are_pytorch_runtime(image: str) -> None:
    assert image.startswith("pytorch/pytorch:")
    assert image.endswith("-runtime")


@pytest.mark.parametrize("image", _DEFAULT_IMAGES)
def test_default_images_satisfy_training_torch_floor(image: str) -> None:
    assert _torch_version(image) >= (2, 9, 1)


@pytest.mark.parametrize("image", _DEFAULT_IMAGES)
def test_default_images_cuda_has_blackwell_kernels(image: str) -> None:
    assert _cuda_version(image) >= (12, 8)
