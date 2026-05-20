# vastrun-kit specification

## Design philosophy

The tool should be so well-designed that agents (and humans) fall into the right path naturally. Failures are unlikely, and when they happen, recovery is self-evident.

## Spec scope

> Authoritative behavioural spec. An implementer working from this file alone can produce a compatible system. Structural decisions (CLI surface, on-disk file shapes, wire-level protocol) are non-negotiable; algorithm and output details are the contract — equivalent behaviour is acceptable.

## Purpose

vastrun-kit wraps the Vast.ai GPU rental marketplace, giving a developer (or AI coding agent) AWS-grade reliability over Vast.ai's raw, cheap, but flaky API. It must:

- Pick a healthy GPU machine that meets a minimum hardware floor.
- Provision it deterministically: one CLI invocation creates at most one billable instance and reaches a known state (SSH ready, marker written) or raises with a recovery hint.
- Tear the instance down when the user is done — and only then.
- Coexist with other agents using the same Vast.ai account on the same workstation, without destroying their instances.
- Provide read-only inspection commands (status, balance) for situational awareness.

The deliverable is a Python package with a set of `vastrun-*` console scripts.

## Multi-tenant model

Threat model: multiple agents and humans running `vastrun-*` concurrently against the same Vast.ai account, from one or many workstations. None of them must be able to destroy another's work.

Two concepts:

- **Hostname.** `socket.gethostname()` of the workstation that provisioned the instance. Captured into the on-instance marker.
- **Label.** Required `--label` to `vastrun-provision`. Captured into the marker; doubles as the Vast.ai display label.

### Ownership scope

**Cross-host only.** A marker whose `hostname` doesn't match this workstation is "not mine." The check does not distinguish two agents on the same workstation — they share the hostname and therefore share ownership identity. Pick distinct `--label` per run to keep co-resident agents from stepping on each other; the label is the second field every destructive command (`vastrun-destroy`, `vastrun-rename`, `vastrun-bid`) cross-checks against the marker before acting.

### Ownership rules

| Operation | Marker check |
|-----------|-------------|
| Rename via `vastrun-rename` | Marker hostname must equal this host. `--force` claims an UNCLAIMED instance only; it never overrides another host's marker. |
| Manual `vastrun-destroy <ID> <LABEL>` | Marker hostname must equal this host AND marker label must equal LABEL. `--force` skips the marker check entirely (and lets the user pass ID alone). |
| `vastrun-destroy --all` (without `--force`) | Always errors with the cross-tenant warning. Doing nothing on the wrong-account case is the safe default. |
| `vastrun-destroy --all --force` | Bulk destroy, no marker check, no prompt. The user has accepted the cross-tenant risk explicitly. |

## Configuration

Three sources: `.vastrun.toml` (per-project), environment, package defaults.

### `.vastrun.toml` (per-project)

Lives in the project root. Required for `vastrun-provision`. Schema:

```toml
[vast]
image             = "your/image:tag"
min_vram_gb       = 24
min_tflops        = 50.0
max_bid           = 1.50
gpu_name          = ["A100", "H100"]   # str or list[str]; substring OR-match
min_reliability   = 0.95
min_upload_mbps   = 500
min_download_mbps = 1000
min_disk_gb       = 100
min_disk_bw       = 500
country           = "US"               # ISO-2 code, case-insensitive
region            = "EU"               # one of EU, US, APAC, NA
ssh_key           = "~/.ssh/id_ed25519.pub"
```

Field semantics:

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `vast.image` | str | package default `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04` | Docker image for new instances. Must be `repo:tag` form. |
| `vast.min_vram_gb` | int | unset → no filter | Per-GPU VRAM floor in GB. |
| `vast.min_tflops` | float | unset → safety floor | Per-GPU TFLOPS floor. |
| `vast.max_bid` | float | unset → safety cap | Max hourly price in $/h. |
| `vast.gpu_name` | str \| list[str] | unset → no filter | GPU model substring(s); OR-matched, case-insensitive, underscore↔space. |
| `vast.min_reliability` | float | unset → safety floor (0.95) | Vast.ai reliability score in [0, 1]. 0.95 floor is non-overridable downward. |
| `vast.min_upload_mbps` | float | unset | Upload-bandwidth floor. |
| `vast.min_download_mbps` | float | unset → bandwidth floor | Download-bandwidth floor. |
| `vast.min_disk_gb` | int | unset → disk floor | Free-disk floor. |
| `vast.min_disk_bw` | int | unset → disk-bw floor | Disk-bandwidth floor. |
| `vast.country` | str | unset | ISO-2 country code (case-insensitive). |
| `vast.region` | str | unset | One of `EU`, `US`, `APAC`, `NA`. |
| `vast.ssh_key` | path | unset → autodiscover | Local SSH public-key file. |

When `.vastrun.toml` is missing, the `client_config` loader raises `FileNotFoundError` whose message names "vastai-kit" / ".vastrun.toml" and points at the README.

### Environment variables

Read in priority order: process environment, then the package directory's `.env`, then the client project's `.env`. Process env wins. `VASTAI_API_TOKEN` is required; if missing from all sources, the loader raises `MissingCredentialError` naming the package `.env` path.

### Package defaults (`config`)

These are used when neither `.vastrun.toml` nor a CLI flag overrides them:

| Constant | Value | Role |
|----------|-------|------|
| `MAX_BID` | `0.80` | Default bid cap in $/h. |
| `BID_MULTIPLIER` | `1.3` | Spot bid = `min(min_bid * 1.3, max_bid)`. |
| `DISK_GB` | `60` | Disk slice requested when creating an instance. |
| `BOOT_TIMEOUT_ITERATIONS` | `60` | × `BOOT_POLL_SECONDS` = 5-min wait for boot. |
| `BOOT_POLL_SECONDS` | `5` | Boot-poll interval. |
| `SSH_READY_RETRIES` | `15` | `wait_for_ssh` poll count. |
| `SSH_READY_DELAY_SECONDS` | `2` | `wait_for_ssh` interval. |
| `SSH_CONNECT_TIMEOUT_SECONDS` | `5` | SSH `ConnectTimeout` for fast probes. |
| `POST_BOOT_GRACE_SECONDS` | `5` | Sleep after `wait_for_boot` before attaching SSH key. |
| `POST_ATTACH_KEY_GRACE_SECONDS` | `3` | Sleep after attaching SSH key before first SSH probe. |
| `ATTACH_SSH_RETRIES` | `3` | Soak transient API hiccups in attach. |
| `ATTACH_SSH_BACKOFF_SECONDS` | `2.0` | Backoff between attach retries. |
| `VASTAI_API_TIMEOUT_SECONDS` | `60` | Hard cap on any single `vastai` subprocess. |
| `SSH_PROBE_TIMEOUT_SECONDS` | `30` | Bound for short-lived SSH probes (hello-world, marker reads). |
| `RELIABILITY_MIN` | `0.95` | Hard floor on reliability. |
| `TFLOPS_PER_GPU_MIN` | `80.0` | Default per-GPU TFLOPS floor. |
| `BANDWIDTH_DOWN_MBPS_MIN` | `500` | Default download-bandwidth floor. |
| `DISK_SPACE_GB_MIN` | `50` | Default disk-space floor. |
| `DISK_BW_MIN` | `500` | Default disk-bandwidth floor. |
| `DIRECT_PORT_COUNT_MIN` | `1` | Direct ports floor — guarantees SSH gets one. |
| `DRIVER_VERSION_MIN` | `"550.0.0"` | Hard NVIDIA driver floor. |
| `CUDA_VERSION_MIN` | `12.4` | Hard CUDA toolkit floor. |
| `IMAGE` | `"nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04"` | Default Docker image for non-Blackwell offers. |
| `BLACKWELL_IMAGE` | `"nvidia/cuda:13.0.0-cudnn-runtime-ubuntu24.04"` | Image auto-selected for Blackwell offers when `--image` / `vast.image` is unset. CUDA 13 has full sm_120 support. |
| `BLACKWELL_GPU_PREFIXES` | `["RTX 50", "RTX PRO 5", "RTX PRO 6", "B200", "B100", "GB200"]` | Offers whose `gpu_name` starts with any of these are treated as Blackwell — `BLACKWELL_IMAGE` is auto-selected when no explicit image is set. |
| `SSH_PUBKEY_CANDIDATES` | `~/.ssh/id_ed25519.pub`, `~/.ssh/id_rsa.pub`, `~/.ssh/id_ecdsa.pub` | Tried in order when no `--ssh-key`. |

## Hard safety floors

Non-overridable. Apply to every offer search.

| Floor | Value | Where enforced |
|-------|-------|---------------|
| CUDA version | >= 12.4 | Post-filter on `cuda_max_good`. Offers without a value pass. |
| NVIDIA driver | >= 550.0.0 | Post-filter on `driver_version`, parsed numerically. Empty values pass — the API already filtered by `cuda_max_good`. |
| Reliability | >= 0.95 | Search query. Users may raise via `min_reliability`; lowering is silently floored. |
| Datacenter | true | Search query (`datacenter=True`). Cleared only when `--prosumer` is passed. |
| Direct ports | >= 1 | Search query. SSH only — vastrun-kit exposes no other ports. |

## Provision flow

Internal flow executed by `vastrun-provision <OFFER_ID>` — stated once here so the per-command section can reference it. Always creates one fresh instance from one specific offer; there is no offer-search, no ranking, no banlist, no flock, and no internal retry.

1. Load `.vastrun.toml` and credentials.
2. Resolve the offer (`vastai search offers id=<OFFER_ID>`). If missing or no longer available, exit 1 with a message naming the offer ID and pointing at `vastrun-search`.
3. Validate SSH key (read, strip, reject empty). Validate image format (must contain `:`). Resolve the effective image: if `--image` / `vast.image` is unset and the offer's `gpu_name` matches `BLACKWELL_GPU_PREFIXES`, use `BLACKWELL_IMAGE`; otherwise `IMAGE`.
4. Check balance. If < $0.10, exit 1 pointing at the billing page.
5. Issue one `vastai create instance` call. On empty stdout (Vast.ai CLI quirk where the create may have succeeded silently), exit 1 with a message directing the user at `vastrun-status` and `vastrun-destroy <id> --force`. **Do not** scan-and-recover — that path has caused duplicate instances. If create errors with a non-empty message, surface it and exit 1.
6. `wait_for_boot` (60 × 5s = 5 min). On timeout / DOA / preemption, exit 1 with a message naming the instance ID, `vastrun-status`, and `vastrun-destroy`.
7. Sleep `POST_BOOT_GRACE_SECONDS`. Attach SSH key (3 retries, 2s backoff). Sleep `POST_ATTACH_KEY_GRACE_SECONDS`. Resolve SSH endpoint. `wait_for_ssh` (15 × 2s).
8. Write the on-instance ownership marker. If the write fails, exit 1 naming the instance ID and pointing at `vastrun-destroy`. The marker must end up on disk — there is no best-effort fallback. The marker is an ownership tag, not a reuse key.
9. Run a hello-world probe (see Failure modes).
10. Print the success summary (format documented under `vastrun-provision`).

Provisioning never auto-destroys, even on failure. Destroy queues at Vast.ai may fire 1-2 hours later and silently kill manually-recovered work; the cost of that is much higher than leaving an orphan.

## Lifecycle commands

Every `vastrun-*` command is a single-command Typer app (no subcommands, no callback) — this shape keeps Typer from inserting a phantom `COMMAND [ARGS]` slot in `--help` and lets `<positional> --flag` parse correctly. Common to all:

- Reads credentials via the env loader; missing `VASTAI_API_TOKEN` exits 1.
- Reads `.vastrun.toml` only when needed (provision; init checks for absence).
- Per-command exit codes are observable contracts.

### `vastrun-init`

```bash
vastrun-init
```

Scaffolds a `.vastrun.toml` template in the current directory. No flags.

Behaviour:

- If `.vastrun.toml` already exists in CWD, exit 1 with an error message naming the filename and "already exists." Do not overwrite.
- Otherwise write a static template containing a commented-out `[vast]` block listing the common knobs.
- Stripping comment lines from the rendered file must yield valid TOML.
- Print "Created .vastrun.toml".
- If no `.env` exists in CWD, print a follow-up tip showing how to create one with `VASTAI_API_TOKEN`.
- If no `pyproject.toml` exists in CWD, print a warning that vastrun expects to run from a Python project root.

Exit codes: 0 on success, 1 if the file already exists.

### `vastrun-search`

```bash
vastrun-search --gpu-model RTX_4090 --max-bid 0.50
vastrun-search --gpu-model "A100,H100" --min-vram 80 --region EU
```

Read-only. List offers matching the given filters as a table the caller (an agent or human) reads to pick one. Provisioning is a separate command that takes a specific offer ID. The agent does the picking — there is no auto-rank-and-create path.

| Flag | Required? | Default | Meaning |
|------|-----------|---------|---------|
| `--gpu-model NAME[,NAME...]` | no | `.vastrun.toml` `vast.gpu_name` | Comma-separated; substring OR-match, case-insensitive, underscore↔space. |
| `--min-vram GB` | no | `.vastrun.toml` `vast.min_vram_gb` | Per-GPU VRAM floor in GB. |
| `--num-gpus N` | no | 1 | Number of GPUs. |
| `--max-bid PRICE` | no | `.vastrun.toml` `vast.max_bid` → `MAX_BID` | Max hourly price in $/h. |
| `--min-tflops F` | no | `.vastrun.toml` `vast.min_tflops` → `TFLOPS_PER_GPU_MIN` | Per-GPU TFLOPS floor. |
| `--min-reliability F` | no | safety floor (0.95) | Minimum reliability score in [0, 1]. |
| `--country CODE` | no | `.vastrun.toml` `vast.country` | ISO-2 country code (case-insensitive). |
| `--region NAME` | no | `.vastrun.toml` `vast.region` | One of `EU`, `US`, `APAC`, `NA`. |
| `--prosumer` | no | off | Allows non-datacenter machines (drops the `datacenter=True` query clause). |
| `--spot` | no | off | Show offers as spot-priced (`min_bid`) instead of on-demand (`dph_total`). |
| `--hardware` | no | off | Show the full machine spec for each offer — appends `CPU` (model + cores), `RAM`, `Bus` (PCIe gen × GPU lanes, e.g. `PCIe 4 ×16`; flags NVLink/SXM when present), `Disk GB`, `Disk BW`, `Inet ↑`, `Inet ↓`, `Driver`, `CUDA`. CPU and Bus are the agent's signal for whether the host can actually feed the GPU (PCIe 3.0 ×4 throttles a 4090; SXM/NVLink scale multi-GPU); the rest fills in capacity and software-stack detail without forcing a fall-back to raw `vastai`. |
| `--limit N` | no | 20 | Cap the number of rows printed. |

Output is a table sorted by best DLPerf-per-dollar first (tiebreak: `min_bid`). Default columns: `Offer ID`, `Machine ID`, `GPU`, `Num`, `VRAM/GPU`, `TFLOPS/GPU`, `$/h`, `DLPerf/$`, `Region`, `Reliability`. With `--hardware`, the extra columns above are appended. The Offer ID is the value passed to `vastrun-provision`.

Exit codes: 0 if at least one offer matched, 1 if no offers matched. The no-match diagnostic must be self-contained — the agent should never need to fall back to raw `vastai` to debug a dry hole. It must:

- Name the **binding filter**: the single user-chosen filter responsible for the most exclusions — only one of `max_bid`, `gpu_name`, `country` / `region`, `min_vram_gb`, `min_upload`/`min_download`, `min_disk_gb`/`min_disk_bw`, or `min_tflops` / `min_reliability` above their safety floors. Phrase it e.g. `Binding filter: max_bid=$0.50/h excluded 87 of 92 candidates.` The hard safety floors (reliability ≥ 0.95, datacenter, driver ≥ 550, CUDA ≥ 12.4, direct ports ≥ 1) are **never** named as the binding filter unless they alone excluded every offer — relaxing them invites hosts that DOA, fail to boot, or crash during early training, which is more expensive than searching longer. The named binding filter is meant to suggest a *safe* relaxation.
- List **every active filter** with the value used and the count it excluded, including the safety floors so the agent sees what's silently in play. Tag `reliability ≥ 0.95`, `driver ≥ 550`, `CUDA ≥ 12.4`, and `direct ports ≥ 1` as `(safety floor — non-negotiable)`. Tag `datacenter=True` as `(safety floor — opt out via --prosumer if you accept preemption risk)`: datacenter is a reliability signal (prosumer hosts can disappear after a few hours), but occasionally a specific prosumer host — e.g. a well-rated Thailand machine — is worth the risk for the right job. User-chosen filters are listed without any tag.
- Print the **total candidate count** before filters and the count surviving each step. The diagnostic must not *recommend* `--prosumer` — datacenter exists for a reason and prosumer instability has cost real training runs. When datacenter=True is what zeroed every offer, state that fact and list `--prosumer` among the *possible* (not recommended) actions; the agent decides whether the tradeoff is worth it. The non-negotiable floors must never be pointed at as a fix.

### `vastrun-provision`

```bash
vastrun-provision 8765432 --label training-v1
vastrun-provision 8765432 --label spot-run --spot
```

Provision exactly one fresh GPU instance from a specific offer ID (typically picked from `vastrun-search` output) and print its ID, hardware summary, SSH endpoint, cost, and label. One CLI invocation creates one new instance; if you want a different machine, run again with a different offer ID.

| Flag | Required? | Default | Meaning |
|------|-----------|---------|---------|
| `OFFER_ID` (positional) | yes | — | Offer ID from `vastrun-search`. |
| `--label NAME` | yes | — | Ownership label; written into the on-instance marker and used as the Vast.ai display label. |
| `--spot` | no | off | Bid as interruptible (`min_bid * BID_MULTIPLIER`, capped at `MAX_BID`). Default is on-demand at `dph_total`. |
| `--image IMAGE` | no | `.vastrun.toml` `vast.image` → `BLACKWELL_IMAGE` if the offer is Blackwell, else `IMAGE` | Docker image override. Must contain `:`. |
| `--ssh-key PATH` | no | autodiscovered from `SSH_PUBKEY_CANDIDATES` | Local SSH public key. |

Behaviour: follows [Provision flow](#provision-flow). On success, print, in this order:

- Empty line.
- `Instance <id> ready`.
- If a hardware summary can be assembled (count + GPU + VRAM + TFLOPS/GPU + geolocation, joined by middle-dot " · "), print `  Hardware: <summary>` on the next line. Omit the line if no fields are available; lookup failure is logged but never blocks the report.
- `  SSH: ssh -p <port> root@<host>`.
- `  Cost: $<price>/h (<spot|on-demand>)` or `unknown`.
- `  Label: <label>`.
- Empty line.
- A "Next:" tip listing `vastrun-exec` and `vastrun-destroy`.

### `vastrun-destroy`

```bash
vastrun-destroy 12345 my-label        # safe path: ID + label, both required
```

Destroy one instance. The `LABEL` positional is a confirmation token: it must match the on-instance owner marker's `label`, otherwise the destroy refuses. This guards against a stray ID taking down the wrong machine.

The user-visible flags are kept minimal on purpose:

| Flag | Required? | Default | Meaning |
|------|-----------|---------|---------|
| `INSTANCE_ID` (positional) | required unless `--all` | — | Instance to destroy. |
| `LABEL` (positional) | required for the single-ID form | — | Must equal the marker's `label`. |
| `--all` | no | off | Bulk path. Without `--force` it ALWAYS errors with the cross-tenant warning (see Behaviour). |

There is **no** `--yes` / `-y`. Destruction never prompts — confirmation is structural (LABEL for single, `--force` for bulk).

`--force` is a real flag accepted by the CLI but is **not** advertised in `--help` or the user-facing README/SKILL docs. Users discover it from the refusal messages, which always tell them precisely how to opt in. The intent: every use of `--force` is a deliberate, eyes-open action — it should not be the path of least resistance from the help text. The flag is documented in this spec because implementers need to know it exists.

| Hidden flag | Default | Meaning |
|-------------|---------|---------|
| `--force` | off | Two distinct uses depending on whether `--all` is set. **Single-ID form**: skip the marker check (label / hostname / missing-marker). Lets the user pass `INSTANCE_ID` alone, without `LABEL`. **Bulk form** (`--all --force`): actually perform the bulk destroy. Without `--force`, `--all` always errors. |

Behaviour:

- **No args, no `--all`** → exit 1 to stderr: `Error: provide an INSTANCE_ID + LABEL, or use --all.`
- **Single ID without `--force` and without `LABEL`** → exit 1 to stderr: `Error: vastrun-destroy <ID> <LABEL> requires both arguments. Pass --force to destroy by ID alone (e.g. when you don't know the label).`
- **Single ID + `LABEL`, no `--force`**: resolve SSH info; read the marker; refuse with exit 1 if any of these hold (each refusal message names what was found vs. expected and tells the user to re-run with `--force`):
  - Marker is missing: `Instance <id> has no marker — it was provisioned outside vastrun-kit. Re-run with --force to destroy anyway.`
  - Marker hostname is not ours: `Instance <id> is owned by host '<other>', not '<me>'. Re-run with --force to destroy anyway.` (See Multi-tenant > Ownership scope.)
  - Marker label is not the provided LABEL: `Instance <id> marker label is '<marker_label>', not '<provided_label>'. Re-run with --force to destroy anyway.`
  - SSH probe to read the marker raises: `Instance <id> SSH unreachable — cannot verify ownership. Re-run with --force to destroy anyway.`
  Otherwise: destroy, verify, report.
- **Single ID + `--force`** (LABEL optional, marker check skipped): destroy, verify, report.
- **`--all` without `--force`** → exit 1 to stderr with the canonical cross-tenant warning, ALWAYS, no exceptions:

  ```
  Error: vastrun-destroy --all is disabled by default — running it would destroy
  every instance the API returns, including instances belonging to other agents
  or co-resident sessions on this account.

  Destroy each of your own instances explicitly with:
      vastrun-destroy <ID> <LABEL>
  for the instances you provisioned (use vastrun-status to list them — the Label
  column shows each instance's owner-marker label).

  If you really mean "destroy everything on this account, regardless of who owns
  it", re-run with --force.
  ```

  This is the only place the `--force` flag for `--all` is named. The error must always print, every time, no exceptions, no prompt.

- **`--all` + `--force`**: enumerate every instance returned by `vastai show instances`. For each, print one heads-up line `<id>  <label or '-'>  <num>× <gpu_name>  uptime <Hh Mm>  spent $X.XX  (<status>)`, then issue destroy and verify. After verification append `  → destroyed` (verified) or `  → WARNING: not confirmed` (unverified) on the same line, or as a follow-up indented line. No marker check. No prompt — `--force` is the explicit "yes". Exit 0 if every destroy verified, 1 if any did not.

- **Reporting after a single destroy**: snapshot the instance via `find_instance` (or `vastai show instance <id>`) immediately *before* issuing the destroy — once the instance is gone the API drops the row, so uptime/cost must be captured up-front. Then:
  - Verified: print a multi-line summary to stdout and exit 0:
    ```
    Destroyed instance <id>
      Label:  <label> or <none>
      GPU:    <num_gpus>× <gpu_name>
      Uptime: <Hh Mm> or <Mm> or <->
      Spent:  $X.XX or <->
    ```
    Missing fields render as `-` / `<none>` rather than blanks; the summary is the agent's only confirmation that the right machine went down and the only place it can capture final cost without a follow-up `vastrun-status`.
  - Unverified: `WARNING: destroy request sent for <id> (label '<label or none>', <num>× <gpu_name>, spent $X.XX) but the API did not confirm termination — the instance may still be billing. Check with: vastrun-status` to stderr, exit 1.

Verification: after issuing `vastai destroy instance <id>`, poll `vastai show instances` for up to 30 seconds (six attempts at 5s each). If the instance is gone or in a terminal state (`exited`, `offline`, `destroyed`, `expired`, or empty), return True. Otherwise issue one more destroy and poll for another 30s. Still running after that → return False; the warning above applies.

The required `LABEL` is what disambiguates concurrent runs on a single host. (Marker scope: see Multi-tenant > Ownership scope.)

### `vastrun-restart`

```bash
vastrun-restart 12345 my-label
```

Restart an instance that Vast.ai has put into `exited` state (host-stopped, host-rebooted, etc). Wraps `vastai start instance <id>` so the user never has to fall back to raw `vastai` for state recovery. Reuses the boot-wait and SSH-probe logic from `vastrun-provision`.

| Flag | Required? | Default | Meaning |
|------|-----------|---------|---------|
| `INSTANCE_ID` (positional) | yes | — | Instance to start. |
| `LABEL` (positional) | required unless `--force` | — | Must match `inst.label` (Vast.ai display label, kept in sync with the marker). The instance is exited so the on-disk marker can't be read; the display label is the ownership signal. |
| `--force` | no (hidden, like `vastrun-destroy --force`) | off | Skip the label check. Documented in error messages, not in `--help`. |

Behaviour:

- Resolve the instance via `find_instance`. If not found, exit 1.
- If `actual_status` is `running` / `loading`, no-op: print `Instance <id> already <status>.` and exit 0.
- Without `--force`, refuse with exit 1 if `inst.label` is missing or does not equal LABEL. Each refusal names what was found vs. expected and tells the user to re-run with `--force`.
- Call `vastai start instance <id>`. On non-zero exit or empty stdout, surface the error and exit 1 (no internal retry).
- `wait_for_boot` (60 × 5s). Same DOA / preempted / terminal-state checks as `vastrun-provision`. On timeout, exit 1 naming the instance ID and pointing at `vastrun-status` and `vastrun-destroy`.
- Resolve the (possibly new) SSH endpoint. `wait_for_ssh` (15 × 2s).
- Print, in this order: empty line, `Instance <id> restarted`, `  SSH: ssh -p <port> root@<host>`, `  Label: <label>`, empty line.

`vastrun-restart` does **not** rewrite the marker — the original owner stays the owner. Restarting under `--force` does not transfer ownership, and the on-instance state remains theirs; restarting someone else's exited instance is **not** a reversible action (it resumes their billing, and destroying it loses their work). Use `--force` only when you are recovering your own instance and the marker was lost.

Exit codes: 0 on success or no-op; 1 on lookup failure, label mismatch, start failure, boot timeout, or SSH timeout.

## Other commands

### `vastrun-status`

```bash
vastrun-status
vastrun-status --watch --interval 10
vastrun-status --json
```

List all instances on the account (regardless of owner).

The default invocation is a single-shot, non-blocking call: it issues one `vastai show instances`, prints the table, and exits immediately — safe to call from agent loops that just need a snapshot. `--watch` turns the same command into a long-running poll that blocks until Ctrl-C; agents may use it, but should treat it as a blocking call (it ties up a session for the duration).

| Flag | Required? | Default | Meaning |
|------|-----------|---------|---------|
| `--json` | no | off | Output the raw `vastai show instances` JSON list. |
| `--watch` / `-w` | no | off | Blocking. Re-run on a fixed interval until Ctrl-C. Implementation must use clear-and-reprint, not Rich `Live`. |
| `--interval N` | no | 5 | Refresh interval in seconds (only with `--watch`). Must be ≥ 1. |

Display columns, in order:

| Column | Source | Format |
|--------|--------|--------|
| ID | `inst.id` | string |
| Label | `inst.label` (Vast.ai display label, kept in sync by `vastrun-provision`/`vastrun-rename`) or `-` | string |
| Status | `actual_status` or `status_msg` or `unknown` | string |
| GPU | `gpu_name` or `-` | string |
| Uptime | `now - start_date` | `Hh Mm` or `Mm` or `-` |
| Cost/h | `dph_total` | `$X.XXXX` or `-` |
| Spent | `(now - start) / 3600 * dph_total` | `$X.XX` or `-` |
| SSH Address | `parse_ssh_endpoint` result | `host:port` or `-` |

The Label column is the user's primary confirmation channel for `vastrun-rename` — after a rename, the next `vastrun-status` snapshot must show the new label.

After the table, print `Total: N instance(s)`. Empty list: print `No running instances found.` and skip the table.

- `--watch`: clear screen and reprint each tick; catch `KeyboardInterrupt` and exit 0.
- `--json`: output `json.dumps(<list>)` of the raw payload, untransformed.

The rendered table contains no Unicode box-drawing characters. Agent consumers must read the table without paying tokens for decoration.

Exit codes: 0 on success.

### `vastrun-balance`

```bash
vastrun-balance
```

Show account credit, balance, and total. No flags.

Behaviour: call `vastai show user`, parse `credit` and `balance` (default 0), print:

```
Credit:  $C.CC
Balance: $B.BB     # only if balance != 0
Total:   $T.TT
```

Exit codes: 0 on success; 1 if credentials missing or the API call errors.

### `vastrun-rename`

```bash
vastrun-rename 12345 training-v2
vastrun-rename 12345 training-v2 --force
```

Relabel an instance. Rewrites the on-instance ownership marker (preserving `created_at`) and updates the Vast.ai display label so both stay in sync.

| Flag | Required? | Default | Meaning |
|------|-----------|---------|---------|
| `INSTANCE_ID` (positional) | yes | — | |
| `NEW_LABEL` (positional) | yes | — | New label. |
| `--force` | no | off | Claim an UNCLAIMED instance (no marker on remote). Does NOT override another host's marker. |

Behaviour:

- Resolve SSH info; exit 1 if not found.
- Read marker.
- If marker is None and `--force`: write a fresh marker with `(hostname=me, label=NEW_LABEL, pid=os.getpid(), created_at=now())`. Then call `vastai label instance`. Print `Claimed UNCLAIMED instance <id> as '<me>'.`.
- If marker is None and not `--force`: print error to stderr that the instance is UNCLAIMED, suggest `--force`, exit 1.
- If marker hostname is **not** `me`: print error to stderr noting the marker is owned by another host and that `--force` does not override another host's marker (see Multi-tenant > Ownership scope), exit 1. Do not write anything.
- If marker hostname **is** `me`: rewrite the marker preserving `created_at`. Print `Owner marker label: '<old>' → '<new>'.`. Then call `vastai label instance`.
- After the marker change, set the Vast.ai display label via `vastai label instance <id> <new_label>`. On failure, print error to stderr with the manual command to retry. Exit 1.
- On success, print `Vast.ai display label set to '<new>'.`.

Exit codes: 0 on success, 1 on any refusal or downstream failure.

### `vastrun-bid`

```bash
vastrun-bid 12345 0.85
```

Change the bid on a spot instance. Wraps `vastai change bid <id> --price <new_bid>`. Lets a user who chose `--spot` raise their bid mid-run to avoid preemption. No-op against on-demand instances.

| Flag | Required? | Default | Meaning |
|------|-----------|---------|---------|
| `INSTANCE_ID` (positional) | yes | — | |
| `NEW_BID` (positional) | yes | — | New hourly bid in $/h. Must be > 0. |

Behaviour:

- Validate `NEW_BID > 0`; reject with exit 1 otherwise.
- Look up the instance via `provision.find_instance`. If not found, exit 1 with a "not found" message.
- If the instance is on-demand (not spot), warn that the bid change has no effect, but proceed (the underlying API accepts it as a no-op).
- Read the on-instance marker and apply the hostname check (see Multi-tenant > Ownership scope). Refuse with exit 1 if the marker is missing, the marker hostname is not this host, or the SSH probe fails. Each refusal names what was found vs. expected.
- Call `vastai change bid <id> --price <new_bid>`. On failure, print error to stderr and exit 1.
- On success, print `Bid for instance <id> set to $<new_bid>/h.`.

Exit codes: 0 on success, 1 on validation failure, lookup failure, or downstream API failure.

### `vastrun-exec`

```bash
vastrun-exec 12345 "nvidia-smi"
```

Run a one-off command on an existing instance, streaming its output.

| Flag | Required? | Default | Meaning |
|------|-----------|---------|---------|
| `INSTANCE_ID` (positional) | yes | — | Target instance. |
| `COMMAND` (positional) | yes | — | Shell command, run via bash. |

Behaviour:

- Load credentials from CWD; no `.vastrun.toml` required.
- Resolve SSH info; exit 1 if not found.
- Wrap the command by base64-encoding and running `echo '<b64>' | base64 -d | bash` over SSH (survives Vast.ai's SSH proxy, which can mangle compound commands).
- Stream output (no capture).
- Exit 255 (SSH failure): retry once after 3s.
- Best-effort `Instance <id>: $X.XX spent so far` print; silent on failure.
- Propagate the command's exit code.

Exit codes: 0 on success; command's exit code otherwise; 1 if the instance is missing.

### `vastrun-forward`

```bash
vastrun-forward show offers
vastrun-forward --force show offers
```

Explicit, friction-y escape hatch to the raw `vastai` CLI. Every fallback to raw `vastai` is a missing-feature signal; this command makes the fallback visible and traceable to a GitHub issue.

| Flag | Required? | Default | Meaning |
|------|-----------|---------|---------|
| `<args>...` (positional) | yes | — | Passed verbatim to `vastai`. |
| `--force` | required to actually forward | off | Without it, the command prints the issue-tracking guidance and exits 1. |

Behaviour:

- Without `--force`: print to stderr a multi-line message that (1) names the situation — "vastrun-forward exists for features not yet covered by another vastrun-* command"; (2) tells the user to first check whether another `vastrun-*` command already does what they need; (3) tells them, if it really is a missing feature, to open an issue at https://github.com/jeremycochoy/vastrun-kit/issues describing the missing feature, then re-run with `--force` to actually forward the call. Exit 1 without forwarding.
- With `--force`: print one line of context up front — "Forwarding to vastai (you should have an issue tracked at https://github.com/jeremycochoy/vastrun-kit/issues for this gap)." — to stderr so the operator audit trail is clear. Then `exec` (or subprocess + propagate) `vastai <args>`, passing through the API key the same way other commands do (read from process env / package `.env` / project `.env`). Stdout, stderr, and exit code from `vastai` pass through unchanged.

Exit codes: 1 when refusing without `--force`; otherwise the exit code of the underlying `vastai` invocation (0 on success, non-zero on `vastai` failure).

## On-instance state

vastrun-kit writes one file on the remote.

### `/tmp/vastrun_owner.json`

The ownership marker. Plain JSON file, written via `echo '<base64>' | base64 -d > /tmp/vastrun_owner.json` to avoid shell quoting headaches.

Schema:

```json
{
  "hostname": "macbook-pro",
  "label": "training-v1",
  "pid": 12345,
  "created_at": "2026-04-29T08:14:00"
}
```

| Field | Source | Notes |
|-------|--------|-------|
| `hostname` | `socket.gethostname()` | Workstation that provisioned (or claimed) the instance. Cross-host check key. |
| `label` | `--label` value | Disambiguates concurrent runs on the same host. Matches Vast.ai display label. |
| `pid` | `os.getpid()` | Process that wrote the marker; informational. |
| `created_at` | ISO 8601 second precision | First write time; preserved across `vastrun-rename`. |

Lifecycle:

- Written by `vastrun-provision` once SSH is up on the new instance. If the write fails, provisioning exits 1 and tells the user to `vastrun-destroy <id> --force`.
- Re-written by `vastrun-rename` on a hostname-match (preserves `created_at`).
- Written by `vastrun-rename --force` on an UNCLAIMED instance.
- Read by `vastrun-destroy`, `vastrun-rename`, and `vastrun-bid` as the ownership sanity-check. A missing/corrupt marker is treated as "no marker" (UNCLAIMED).
- Never deleted by vastrun-kit. Disappears with the instance.

## Offer search

Used by `vastrun-search`. Composes a query for `vastai search offers <query>` and filters the JSON response. There is no ranking or auto-pick step — the agent reads the listing and chooses an offer ID.

### Query

```
reliability>{R} direct_port_count>={1} disk_space>={D} disk_bw>={DB} inet_down>{DL}
[datacenter=True]               # when --prosumer is OFF
[inet_up>{UL}]                  # when --min-upload-mbps is set
num_gpus>={G}
total_flops>={TPG * G}
[gpu_ram>={V}]                  # when --min-vram is set
```

`R`, `D`, `DB`, `DL`, `TPG` come from the filter (or safety floors). `G` defaults to 1. `V`, `UL` only emitted when set.

### Filter

Server-side query is best-effort. Apply these post-filters to the JSON response — exclude offers where:

- Effective price (`min_bid` if `spot`, else `dph_total`) exceeds `max_bid`.
- `driver_version` < 550 (numeric; empty/missing passes).
- `cuda_max_good` < 12.4 (zero/missing passes).
- `geolocation` country code doesn't match `country`. Geolocation looks like `"United States, US"`; the ISO code is the trailing token.
- Country isn't in the resolved region set (when `region` is set).
- `inet_up` / `inet_down` below per-filter floors.
- Per-GPU TFLOPS (`total_flops / num_gpus`) below the per-GPU floor.
- Per-GPU VRAM below `min_vram_gb`. The API's `gpu_ram` is already per-GPU (MB); compare it directly — do **not** divide by `num_gpus` (the all-GPU total is the separate `gpu_total_ram` field).
- `gpu_name` doesn't match any user pattern (substring OR-match, case-insensitive, underscore↔space).

Sort survivors by `(-dlperf_per_dphtotal, min_bid)` — best DLPerf-per-dollar first, with `min_bid` as a tiebreaker. `vastrun-search` prints up to `--limit` rows in that order; the agent sees the highest-value offers at the top.

### Region-to-country mapping

Built-in mapping; passing an unknown region raises `ValueError`:

| Region | Country codes |
|--------|---------------|
| `EU` | DE, NL, FR, GB, SE, FI, NO, PL, CZ, AT, IT, ES, CH, IE, BE, DK |
| `US` | US |
| `APAC` | JP, KR, SG, AU, TW, HK, IN |
| `NA` | US, CA |

## Error messages

Errors are the primary documentation. When something goes wrong, the message must be clear, transparent, and contain every fact needed to act on it — instance IDs, offer IDs, file paths, the value that failed validation. Whenever a recovery action exists, the error names it, and the recommended path is the safe one (e.g. `vastrun-provision 99999999 --label v1` failing because the offer is gone points the user at `vastrun-search` to list current offers, not at `--force`). No "check the docs", no opaque codes.

## Failure modes & recovery

### Create failures

- **Empty stdout from `vastai create instance`**: a known Vast.ai CLI bug where the create may have succeeded silently. Exit 1 with a message naming the offer ID and pointing at `vastrun-status` (to find any leaked instance) and `vastrun-destroy <id> --force` (to clean it up). Do **not** scan-and-recover — that path has historically caused duplicate instances to be adopted and re-billed.
- **`find_instance` raising `RuntimeError` during the hardware-summary lookup**: decorative only. Must NOT swallow the SSH endpoint of an already-billing instance. Print a short note and continue.
- **Bad image format** (`--image foo` with no colon): exit 1 before issuing the create call.

### Boot failures

- **DOA detection**: when `status_msg` contains the substring `"error"` (case-insensitive) before the instance reaches `running`, fail immediately on the next poll. Don't wait the full 5-minute timeout for an obviously-broken docker_build, etc.
- **Outbid / preempted before running**: when `intended_status == "stopped"`, fail.
- **Terminal states** (`exited`, `offline`, `destroyed`, `expired`): fail.
- **Boot timeout**: fail after `BOOT_TIMEOUT_ITERATIONS * BOOT_POLL_SECONDS` seconds.

In every case, exit 1 with a message naming the instance ID, the observed status, and the recovery commands (`vastrun-status`, `vastrun-destroy <id> --force`). The instance is left alive — the operator decides whether to keep it (rare) or destroy it.

### SSH-attach failures

`attach_ssh_key` retries up to 3 times with 2s backoff on:

- Non-zero exit from `vastai attach ssh`.
- Stdout containing `"'success': false"` (case-insensitive). Common payload: `{'success': False, 'msg': 'SSH key already associated with instance.'}` — often clears on retry; if permanent, the original message bubbles up.

On exhaustion, exit 1 naming the instance ID and the recovery commands.

### SSH-info / wait-for-ssh failures

If `get_ssh_info` returns None and `get_ssh_url_fallback` (parsing `vastai ssh-url`) also returns None, exit 1 with `"Instance N created but SSH info missing from API. Run vastrun-destroy <id> --force to clean up."`.

If `wait_for_ssh` returns False after 15 × 2s tries, exit 1 with `"Instance N created but SSH unreachable at host:port. Run vastrun-destroy <id> --force to clean up."`.

### Marker write failure during provisioning

If the marker write raises `RuntimeError` or `OSError`, exit 1 with a message that names the instance ID, mentions "marker"/"ownership", and tells the user to `vastrun-destroy <id> --force`. The instance must NOT be silently kept — without a marker, every destructive command refuses to act on it, and it leaks until manually destroyed.

### Vast.ai CLI quirks

Wrapping rules for the upstream `vastai` PyPI CLI:

- Every subprocess call has a hard 60s timeout (`VASTAI_API_TIMEOUT_SECONDS`).
- Empty stdout from `--raw` is a CLI bug. Treat as failure and exit 1 with a recovery hint; do not scan-and-recover.
- Invalid JSON from `--raw` raises with the first 200 chars of output.
- Stderr from a failed call is appended to the error message.

`find_instance` walks the full `show instances` list; if `list_instances` raises, the error propagates (callers like the provision CLI's hardware-summary lookup catch and continue).

## Internal architecture

Implementations are free to organize the package internally — module names, helper layout, and import topology are not part of the spec. The CLI surface (one single-command Typer app per `vastrun-*` script), on-disk shapes (`.vastrun.toml`, `/tmp/vastrun_owner.json`), and the behaviours documented above are the contract.

## Known limitations

- **Cross-host only marker check.** See Multi-tenant > Ownership scope. Co-resident agents picking the same label can step on each other.
- **SSH key handling.** Single key read once at provision time. Auto-discovers one of `~/.ssh/id_ed25519.pub`, `~/.ssh/id_rsa.pub`, `~/.ssh/id_ecdsa.pub`. No multi-key, key rotation, or non-`root` login.
- **No batch rename / claim.** `vastrun-rename` is one-at-a-time.
- **No persistent local state.** Truth is Vast.ai API + on-instance marker. A `vastrun-destroy` that fails to ack leaves the user one `vastrun-status` away from the truth — by design.
- **`vastrun-destroy --force` skips the ownership check.** Explicit "I want this gone" path. Wrap with the multi-tenant guards documented in the README/CLAUDE.md.
- **Watch mode uses clear-and-reprint, not Rich `Live`.** Rich `Live` renders box-drawing chars unsuitable for agent consumers.

## Glossary

- **Marker** — `/tmp/vastrun_owner.json` on the rented instance. Identifies the workstation+label that provisioned or claimed it. Cross-host check key.
- **Owner / ownership** — A *host* owns an instance when its hostname matches the marker. *Label* disambiguates runs on the same host — every destructive command requires it as confirmation.
- **Label** — Required `--label` to `vastrun-provision`. Becomes the marker's `label` and the Vast.ai display label.
- **Offer** — One bookable Vast.ai listing. Identified by `offer.id`, tied to a `machine_id`.
- **Instance** — A live or formerly-live rental backed by an offer. Identified by `instance_id`.
- **Machine** — The physical host. Identified by `machine_id`. Multiple offers may map to one machine.
- **Prosumer** — Vast.ai's term for non-datacenter hosts. Excluded by default; `--prosumer` opts in.
- **Spot** — Interruptible bidding. User bids `min_bid * BID_MULTIPLIER` (capped at `max_bid`). Default is on-demand at `dph_total`. `--spot` opts in.
- **DOA** — Dead on arrival. Instance errors out during boot. Detected via `error` substring in `status_msg`; fails immediately.
- **LOCwT** — Lines Of Code without Tests. Simplicity budget. Each PR's diff defends its LOC delta.
