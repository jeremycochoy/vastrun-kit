# Vast.ai CLI quirks

Empirically verified against `vastai==1.0.8`. Each item documents a behaviour
of the upstream CLI that vastrun-kit had to work around. Keep this file
updated as the upstream evolves; if you find an item no longer applies,
remove the workaround in the linked code and delete the entry.

## `vastai search offers id=<X>` is a no-op

The `id` field in the search query language refers to *instance* ids
(rentals), not offer / contract ids. `vastai search offers id=N --raw`
always returns `[]`, even for offer ids that exist and are rentable.
SPEC.md's "Provision flow" step 2 was written assuming the filter worked.

Workaround: `provision.resolve_offer` replays the package's safety-floor
query (the same one `vastrun-search` emits) and finds the offer with a
client-side scan of the result rows. The query reuses
`offers.OfferFilters()` + `build_offer_query`, so there is no duplication
between search and provision.

The cost is one extra `--limit 10000` call per provision. Acceptable for
a one-shot operation.

## A blank `vastai search offers --limit 10000` returns a scored slice

The upstream CLI does not page through the full marketplace when given a
high limit. Empirically the response caps around ~1000–4000 rows even
with `--limit 10000`, and the rows are an internally-ranked subset.
Specific offers shown by `vastrun-search` moments earlier are routinely
absent from a blank-query result.

Workaround: target searches with the package's safety-floor query —
`reliability>0.95 direct_port_count>=1 disk_space>=50 disk_bw>=500 inet_down>500 num_gpus>=1 total_flops>=80 datacenter=True`
— consistently include datacenter offers the user just chose.
`provision.resolve_offer` uses this same query.

Note: prosumer offers (`datacenter=False`) are deliberately not searched
by `resolve_offer`. The package treats datacenter as a safety floor; if
the user is on the spec'd path they only ever pass `vastrun-search`'s
default datacenter offers to `vastrun-provision`. Provisioning prosumer
offers is not currently a supported flow (it would need a new flag on
`vastrun-provision`, which the spec does not include).

## `vastai destroy instance <id>` prompts for confirmation by default

Without `-y`, the upstream CLI prints
`Are you sure you want to destroy instance <id>? ... [y/N]`
and aborts when stdin is non-interactive (`Aborted.`). Both call sites in
`destroy.py` (single-instance and the `verify_destroyed` second-attempt)
pass `-y`. Tests assert the `-y` is in the argv.

## `vastai attach ssh` returns `'success': False` for the success state

When the host pre-attaches the user's key during boot, a subsequent
`vastai attach ssh <id> <pubkey>` call returns:
```json
{"success": false, "msg": "SSH key already associated with instance."}
```
The post-condition we want — the key is on the instance — is already
met, but the message never clears on retry. The original retry loop
exhausted on this and exited 1 on a working instance.

Workaround: `ssh.attach_ssh_key` treats any stdout containing
`already associated` (case-insensitive) as success — no retry, no raise.
A regression test covers this in `test_ssh.py`.

## `start_date` in `vastai show instances --raw` is a Unix epoch float

Not ISO-8601. Always parse with `time.time() - float(start)`. Calling
`datetime.fromisoformat` will silently raise `ValueError` and any
"best-effort" wrapper around it stays silent forever. The `vastrun-exec`
spent-so-far footer originally got this wrong; fixed to use
`time.time()` arithmetic, with a regression test in `test_cli_exec.py`.

## `vastai show user --raw` works in 1.0.8 but failed in 1.0.3

vastai 1.0.3 returned
`Failed with error 400: owner: Extra inputs are not permitted`
on `vastai show user --raw`. `vastai>=1.0.8` returns clean JSON with
`credit` and `balance` fields. The package's dependency floor is set to
`vastai>=1.0.8` for this reason; `vastrun-balance` would not work
against older releases.
