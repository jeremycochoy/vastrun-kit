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
  falls through to a default image that works on it;
- README.md, docs/SPEC.md and the `vastrun-init` template name the current
  defaults — an image bump that forgets the docs fails here instead of
  drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vastrun_kit import config

_REPO = Path(__file__).resolve().parent.parent

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


@pytest.mark.parametrize("doc", ("README.md", "docs/SPEC.md"))
@pytest.mark.parametrize("image", _DEFAULT_IMAGES)
def test_docs_name_the_current_default_images(doc: str, image: str) -> None:
    assert image in (_REPO / doc).read_text(), f"{doc} does not mention {image}"


def test_init_template_names_the_current_default_image() -> None:
    from vastrun_kit.cli import init

    assert config.IMAGE in init._TEMPLATE
