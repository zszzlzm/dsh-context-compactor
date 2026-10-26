# dsh-tool-router — local fix notes

Vendored copy of [DSH-Tool-Router](https://github.com/residenthiago2011/DSH-Tool-Router)
v1.1.0 (MIT) **plus one behavioural fix**, so this workspace can run the
`no-prefill` preset on a profile that also exposes agent-scoped team tools.

Upstream PR: https://github.com/residenthiago2011/DSH-Tool-Router/pull/1

## What was broken

`recompute()` passes the whole probed surface to `agent.ctx.tools.restrict({ deny })`.
DSH validates every name against the **global** registry and throws the entire call
if one name is unknown there. Team tools (`send_message`, `list_agents`,
`interrupt_agent`) are registered per agent, so one of them poisoned the call:

```text
restrict failed: tools.restrict() names unknown global tools
  "interrupt_agent", "list_agents", "send_message";
  known global tools: bash, create_worktree, ... x_search
adopted 8115c13a: globalView=24 agentSurface=27 -> PENDING
  ... retried every ~510 ms, 20 times ...
gave up arming mask
```

The mask never armed, so every tool schema stayed in prefill — the exact opposite of
the preset's purpose.

## Measured on device (qwen3.8:27b-mtp-q4_K_M, 8192 window)

| | systemTokens | toolsTokens | pressure / window |
|---|---:|---:|---|
| before | 174 | **5837** | 6654 / 8192 |
| after | 174 | **1404** | **2073 / 8192** |

## What the fix does

- Reconciles `deny` against the admission list the rejection itself reports
  (`known global tools: …`) and retries; unregistered names stay visible.
- Deliberately **no** remembered exclusion list: while tool plugins mount, the
  admission list is briefly short, so remembering rejections blacklists valid tools
  forever (measured: deny stuck at 14 of 24). Recomputing on each `tools/change`
  converges instead.
- Nulls the disposer after lifting it, so a rejected attempt cannot make the
  key check conclude "already armed".

## Tests

```text
node restrict-scope-test.mjs   # ALL_PASS — agent-scoped surface + late registry
node router-e2e-test.mjs       # ALL_PASS — upstream suite, unchanged
```

`router-e2e-test.mjs` cannot catch this bug: its stub `restrict()` only checks that
`deny` is non-empty, never that the names exist globally. That is why the suite was
green while every real session was unmasked.

## Extra local-only bit (not in the PR)

The copy installed in the running profile also mirrors `log()` to
`%USERPROFILE%\.dsh\tool-router.log` (override with `DSH_TOOL_ROUTER_LOG`), because
DSH does not persist plugin stderr. That is diagnostic-only and is **not** part of the
upstream patch. Backups of the pre-patch file: `index.js.bak-*`.
