# vastrun-kit

AWS-grade reliability for GPU compute at Vast.ai prices. Wraps the Vast.ai CLI with safety filters, on-instance ownership markers, and a small set of `vastrun-*` commands that compose into a search → provision → run → destroy flow.

## Design philosophy

The tool should be so well-designed that agents (and humans) fall into the right path naturally. Failures are unlikely, and when they happen, recovery is self-evident.

## Why

Vast.ai is cheap (RTX 4090 ≈ $0.35/h) but the raw API is flaky — machines fail to boot, SSH keys don't attach, drivers are stale, instances get outbid. vastrun-kit wraps it with:

- **Hard safety floors** on reliability, drivers, CUDA, datacenter, direct-port count (non-overridable, see below).
- **Two-step flow**: `vastrun-search` lists offers, you (or your agent) pick one, `vastrun-provision <OFFER_ID> --label NAME` creates exactly one instance from it.
- **On-instance ownership marker** so co-resident agents on the same Vast.ai account don't destroy each other's work.

## Install

```bash
git clone git@github.com:jeremycochoy/vastrun-kit.git
cd vastrun-kit
echo 'VASTAI_API_TOKEN=<your key>' > .env
uv tool install --editable .
```

Sign up at [vast.ai](https://vast.ai/), top up, and add your SSH public key under Keys.

## Quick start

```bash
cd ~/projects/your-project
vastrun-init                                              # scaffold .vastrun.toml

vastrun-search --gpu-model RTX_4090 --max-bid 0.50        # pick an Offer ID
vastrun-provision 8765432 --label training-v1
# => Instance 12345 ready

vastrun-exec 12345 "nvidia-smi"
vastrun-destroy 12345 training-v1
```

## CLI reference

Every command is a standalone single-command Typer script.

### Lifecycle

| Command | What it does |
|---------|--------------|
| `vastrun-init` | Scaffold `.vastrun.toml` (and warn if no `.env` / `pyproject.toml` in CWD). |
| `vastrun-search [filters]` | Read-only. List matching offers as a table sorted by DLPerf-per-dollar. The agent picks one. No-match prints a self-contained diagnostic naming the binding filter. |
| `vastrun-provision OFFER_ID --label NAME [--spot] [--image IMAGE] [--ssh-key PATH]` | Create one fresh instance from an Offer ID. Writes the on-instance ownership marker. One CLI invocation creates at most one billable instance. |
| `vastrun-destroy ID LABEL` / `vastrun-destroy --all` | Destroy one instance. `LABEL` must equal the marker label (confirmation token); otherwise the destroy refuses. `--all` errors by default with a cross-tenant warning — bulk destroy is opt-in and explicit. |
| `vastrun-restart ID LABEL` | Start an instance Vast.ai has put into `exited` (host-rebooted, etc). Reuses provision's boot-wait + SSH-probe. Does NOT rewrite the marker; original owner stays the owner. |
| `vastrun-rename ID NEW_LABEL [--force]` | Relabel an instance you own. Rewrites the marker (preserving `created_at`) and updates the Vast.ai display label so both stay in sync. `--force` claims an UNCLAIMED instance; never overrides another host's marker. |
| `vastrun-bid ID NEW_BID` | Raise/lower the bid on a spot instance to avoid preemption. No-op on on-demand instances. Hostname-checks the marker before acting. |
| `vastrun-exec ID "command"` | Run a one-off shell command, streaming output. Propagates the command's exit code. |

### Inspection

| Command | What it does |
|---------|--------------|
| `vastrun-status [--watch] [--interval N] [--json]` | List every instance on the account. Default is one-shot and non-blocking — safe from agent loops. `--watch` blocks and reprints every `--interval` seconds (default 5). `--json` emits the raw API payload. Columns: ID, Label, Status, GPU, Uptime, Cost/h, Spent, SSH Address. |
| `vastrun-balance` | Show account `Credit`, `Balance` (if non-zero), `Total`. |

### Escape hatch

| Command | What it does |
|---------|--------------|
| `vastrun-forward <args>...` | Friction-y passthrough to raw `vastai`. Refuses by default and tells you to first check whether another `vastrun-*` covers your need, then to open an issue if it doesn't. Forwarding leaves an audit-trail line on stderr. Every fallback to raw `vastai` is a missing-feature signal. |

### `vastrun-search` filters

| Flag | Default | Meaning |
|------|---------|---------|
| `--gpu-model NAME[,NAME...]` | `vast.gpu_name` | Substring OR-match, case-insensitive, underscore↔space. |
| `--min-vram GB` | `vast.min_vram_gb` | Per-GPU VRAM floor. |
| `--num-gpus N` | 1 | Number of GPUs. |
| `--max-bid PRICE` | `vast.max_bid` → `MAX_BID` ($0.80/h) | Max hourly $/h. |
| `--min-tflops F` | `vast.min_tflops` → `TFLOPS_PER_GPU_MIN` (80) | Per-GPU TFLOPS floor. |
| `--min-reliability F` | safety floor (0.95) | Floored at 0.95; raises only. |
| `--country CODE` | `vast.country` | ISO-2 country, case-insensitive. |
| `--region NAME` | `vast.region` | One of `EU`, `US`, `APAC`, `NA`. |
| `--prosumer` | off | Allow non-datacenter machines (preemption risk). |
| `--spot` | off | Show spot prices (`min_bid`) instead of on-demand (`dph_total`). |
| `--hardware` | off | Append CPU, RAM, PCIe bus / NVLink, disk, internet, driver, CUDA columns. |
| `--limit N` | 20 | Cap rows printed. |

Default columns: `Offer ID`, `Machine ID`, `GPU`, `Num`, `VRAM/GPU`, `TFLOPS/GPU`, `$/h`, `DLPerf/$`, `Region`, `Reliability`. The Offer ID is what you pass to `vastrun-provision`.

## Hard safety floors (non-overridable)

| Floor | Value | Where enforced |
|-------|-------|----------------|
| Reliability | ≥ 0.95 | Search query. Users may raise; lowering is silently floored. |
| Datacenter | true | Search query. Drop with `--prosumer` only if you accept preemption. |
| Direct ports | ≥ 1 | Search query (SSH only — vastrun-kit exposes no other ports). |
| NVIDIA driver | ≥ 550.0.0 | Post-filter on `driver_version`. |
| CUDA | ≥ 12.4 | Post-filter on `cuda_max_good`. |

## `.vastrun.toml`

Per-project config in CWD. Required for `vastrun-provision`. Scaffold one with `vastrun-init`. All keys are optional unless noted.

```toml
[vast]
image             = "your/image:tag"     # repo:tag form; default: nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
                                         # Blackwell offers (RTX 50/PRO 5/PRO 6, B100/B200, GB200) auto-promote to
                                         # nvidia/cuda:13.0.0-cudnn-runtime-ubuntu24.04 when image is unset
min_vram_gb       = 24
min_tflops        = 50.0
max_bid           = 1.50
gpu_name          = ["A100", "H100"]     # str or list[str]; substring OR-match
min_reliability   = 0.95                 # cannot be lowered below 0.95
min_upload_mbps   = 500
min_download_mbps = 1000
min_disk_gb       = 100
min_disk_bw       = 500
country           = "US"                 # ISO-2, case-insensitive
region            = "EU"                 # EU | US | APAC | NA
ssh_key           = "~/.ssh/id_ed25519.pub"
```

`VASTAI_API_TOKEN` is read from process env, the package directory's `.env`, or your project's `.env`, in that order. Process env wins.

**PyTorch pinning:** the default image is CUDA 12.8, so a bare `pip install torch` may pull a wheel for a different CUDA. Pin to cu128, or set `image = "pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime"`.

## Multi-agent safety

vastrun-kit's threat model: multiple agents and humans running `vastrun-*` concurrently against the same Vast.ai account, from one or many workstations. The mechanism:

- Every provision writes `/tmp/vastrun_owner.json` on the rented instance: `{hostname, label, pid, created_at}`.
- **Cross-host ownership only.** A marker whose `hostname` (`socket.gethostname()`) doesn't match this workstation is "not mine" — `vastrun-destroy`, `vastrun-rename`, and `vastrun-bid` refuse to act on it. Two agents on the same workstation share the hostname and therefore share ownership identity.
- **Same-host disambiguation is the label.** Pick distinct `--label` per run. `vastrun-destroy <ID> <LABEL>` cross-checks the label against the marker before destroying.
- **`vastrun-destroy --all` errors by default** with a cross-tenant warning that lists the instances and tells you to destroy each one explicitly. The bulk path is opt-in.
- **Restarting under the marker-skip path does NOT transfer ownership** — `vastrun-restart` never rewrites the marker. Restarting someone else's exited instance resumes their billing and is not reversible.

## How a provision actually goes

`vastrun-provision <OFFER_ID> --label NAME` creates one fresh instance from one specific offer. There is no offer-search, no reuse, no banlist, no flock, no internal retry:

1. Load `.vastrun.toml` + credentials. Resolve the offer via `vastai search offers id=<OFFER_ID>`; if it's gone, exit 1 pointing at `vastrun-search`.
2. Validate SSH key (read, strip, reject empty) and image format (must contain `:`). Resolve the effective image: Blackwell offer + no explicit image → `BLACKWELL_IMAGE`; otherwise default `IMAGE`.
3. Refuse if account credit < $0.10.
4. One `vastai create instance` call. Empty stdout (a known CLI bug where create may have succeeded silently) → exit 1 with a recovery message naming the offer ID. **No scan-and-recover** — that path has historically caused duplicate instances.
5. `wait_for_boot` (60 × 5s = 5 min). DOA (`error` substring in `status_msg`), outbid (`intended_status == stopped`), terminal state, or boot timeout → exit 1 with the instance ID and recovery commands.
6. Sleep, attach SSH key (3× retry, 2s backoff), sleep, resolve SSH endpoint, `wait_for_ssh` (15 × 2s).
7. Write `/tmp/vastrun_owner.json`. The marker MUST end up on disk — without it every destructive command refuses to act on the instance, so on write failure the error message names the explicit recovery command.
8. Hello-world probe; print summary (instance ID, hardware, SSH command, cost, label) and a Next-tip.

Provisioning never auto-destroys, even on failure. Vast.ai's destroy queue can fire 1–2 hours later and silently kill manually-recovered work; the cost of that is far higher than leaving an orphan. Failure messages always name the instance ID and the recovery command — there is no "check the docs."

## Testing

```bash
uv run pytest tests/ -x              # unit tests, no API key needed
uv run pytest tests/ -m integration  # offer-search live calls, needs VASTAI_API_TOKEN
```
