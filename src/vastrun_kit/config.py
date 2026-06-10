"""Package-level constants. No logic — just the spec's tunables in one place."""

from __future__ import annotations

MAX_BID = 0.80
BID_MULTIPLIER = 1.3
DISK_GB = 60

BOOT_TIMEOUT_ITERATIONS = 60
BOOT_POLL_SECONDS = 5
SSH_READY_RETRIES = 15
SSH_READY_DELAY_SECONDS = 2
SSH_CONNECT_TIMEOUT_SECONDS = 5
POST_BOOT_GRACE_SECONDS = 5
POST_ATTACH_KEY_GRACE_SECONDS = 3
ATTACH_SSH_RETRIES = 3
ATTACH_SSH_BACKOFF_SECONDS = 2.0
VASTAI_API_TIMEOUT_SECONDS = 60
SSH_PROBE_TIMEOUT_SECONDS = 30

RELIABILITY_MIN = 0.95
TFLOPS_PER_GPU_MIN = 80.0
BANDWIDTH_DOWN_MBPS_MIN = 500
DISK_SPACE_GB_MIN = 50
DISK_BW_MIN = 500
DIRECT_PORT_COUNT_MIN = 1
DRIVER_VERSION_MIN = "550.0.0"
CUDA_VERSION_MIN = 12.4

IMAGE = "pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime"
BLACKWELL_IMAGE = "pytorch/pytorch:2.9.1-cuda13.0-cudnn9-runtime"
BLACKWELL_GPU_PREFIXES = ("RTX 50", "RTX PRO 5", "RTX PRO 6", "B200", "B100", "GB200")

SSH_PUBKEY_CANDIDATES = (
    "~/.ssh/id_ed25519.pub",
    "~/.ssh/id_rsa.pub",
    "~/.ssh/id_ecdsa.pub",
)

REGION_TO_COUNTRIES = {
    "EU": ["DE", "NL", "FR", "GB", "SE", "FI", "NO", "PL", "CZ", "AT", "IT", "ES", "CH", "IE", "BE", "DK"],
    "US": ["US"],
    "APAC": ["JP", "KR", "SG", "AU", "TW", "HK", "IN"],
    "NA": ["US", "CA"],
}

ISSUE_URL = "https://github.com/jeremycochoy/vastrun-kit/issues"
