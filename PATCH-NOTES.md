# dsh-tool-router — local fix notes

Vendored copy of [DSH-Tool-Router](https://github.com/residenthiago2011/DSH-Tool-Router)
v1.1.0 (MIT) plus a behavioural fix, so this workspace can run the `no-prefill`
preset on a profile that also exposes agent-scoped team tools.

Upstream PR: https://github.com/residenthiago2011/DSH-Tool-Router/pull/1

## What was broken

`recompute()` passes the whole probed surface to `agent.ctx.tools.restrict({ deny })`.
DSH validates every name against the **global** registry and throws the entire call
if one name is unknown there. Team tools (`send_message`, `list_agents`,
`interrupt_agent`) are registered per agent, so one of them poisoned the call and
the mask never armed — every schema stayed in prefill.

## Measured on device (qwen3.8:27b-mtp-q4_K_M, 8192 window)

| | systemTokens | toolsTokens | pressure / window |
|---|---:|---:|---|
| before | 174 | **5837** | 6654 / 8192 |
| after | 174 | **1404** | **2073 / 8192** |

## The fix, and the trap inside the fix

Reconcile `deny` against the admission list the rejection reports
(`known global tools: …`). Two things are easy to get wrong:

1. **A remembered blacklist is wrong.** While tool plugins mount, the admission list
   is briefly short, so remembering "rejected" names blacklists valid tools forever
   (measured: deny stuck at 14 of 24).
2. **A memo is mandatory.** `restrict()` fires `tools/change` synchronously, so each
   successful arm re-enters `recompute()`. Without a memo that re-entry restarts from
   the un-reconciled surface, throws, lifts the good mask and re-arms — forever:
   ~2800 arm cycles/s on one core, 8k log lines/s, 111 MB of log in 13 minutes.

The vendored code memoises the reconciled set per **exact surface** (sorted names),
which solves both: a different (smaller) surface is a different key.

## Tests

```text
node restrict-scope-test.mjs   # ALL_PASS — 3 scenarios, incl. synchronous re-entry
node router-e2e-test.mjs       # ALL_PASS — upstream suite, unchanged
```

Scenario 3 is the one that matters: it emits `tools/change` from inside the stub
`restrict()`, exactly like the device. Without the memo it fails
(`restrictCalls=3, rejects=3`, deny oscillating 24↔1). The upstream
`router-e2e-test.mjs` cannot catch any of this: its stub never validates names
against a global registry.

## Extra local-only bit (not in the PR)

The copy installed in the running profile also mirrors `log()` to
`%USERPROFILE%\.dsh\tool-router.log` (override with `DSH_TOOL_ROUTER_LOG`), because
DSH does not persist plugin stderr. Diagnostic only — **and it made the storm far
worse on disk** (8k lines/s). Not part of the upstream patch.
