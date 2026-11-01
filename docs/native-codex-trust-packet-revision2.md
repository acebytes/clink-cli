# Codex trust packet: fresh MCP metadata fixture

✅ High confidence in these static byte bindings. The user's reopened native verification request and standing authorization cover this bounded replacement after the [attempt 3 diagnosis](native-codex-attempt3-diagnosis.md). No additional permission question is pending. This packet grants no production admission and does not attest native trust, execution model, or effective runtime permissions.

Exact canonical replacement root:

```text
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt4-prepare.fixture
```

The [prepare receipt](verification/native-codex-0.166.0-attempt4-prepare.json) (`b0184c3bfd4a310d90a6a23c204ec3d97683a45a338af90d5afc15d2cd62077c`) and [preflight](verification/native-codex-0.166.0-attempt4-prepare.preflight.json) (`0d042935849822936c0b201492ea95d03f03d10a674a3d5f25cbcf88d825a651`) retain the failed attempt 3 ledger. Preparation submitted zero model turns and performed no trust enrollment. The cumulative allowance is five: one consumed, followed by one fresh four-check sequence after verification/review, stopping at its first failed gate.

Bindings: `codex-cli 0.166.0`; executable SHA-256 `39acf979729dd290f9a24ee38b2914e036c91b65952872e28ae2a6b88a7db7c1`; unchanged native recipe `623312bcc2e1f45f831edc1eb4a77598d25460ff0f9a4ce52b0c6c3ec1fbb1ed`; root fingerprint `a6769ad81e6774db0419cfa32885ff576a6fb531988052b8db2d89999ca73f9a`; full 12-file aggregate `6d5b6637b4bcf88de20375b2de2417625f0aedaf3e045b4f7cb34d888b220f36`.

| Prepared file relative to the exact root | SHA-256 |
|---|---|
| `.clink-native-verification-fixture` | `e8c6a171e39f6736e0bed5b83d33b58bcaffdc1d56052363d109806173cbcace` |
| `.codex/agents/fixture-specialist.toml` | `a0d77681e4ae0747a14ecdc3bc804ca228cf46b14bb8287762a0c590278456f3` |
| `.codex/config.toml` | `df848231abf2c8bae80aef10c33a93c09872687a33b88f902608a9dd8e2c98ce` |
| `.codex/hooks.json` | `4aef088952536f0847667d4ea41defbc9ca854e084e3c0642bfb7673ea6a24e1` |
| `.codex/rules/native-fixture.rules` | `5e14c4ce793a0e520faf405ee6119955c5e84e549671c0d7ef2c170404843c08` |
| `AGENTS.md` | `6ced3b1036ddbc48cc3cdff4f8b09ca60cad1eeb03b07f6fe678c7ab3cfb7894` |
| `codex-specialist.txt` | `4a8bbf39966ea35435f35bf23c227b229d10c7dcb6e2e8a64e1fbf0b27072447` |
| `codex_hook.py` | `943f26e6fd756b9984749c5fe4b53ecdcbc8ae56a060a8f4f24810200e0f3a42` |
| `codex_mcp_server.py` | `45844bad6648e9fb01a0ae32f0662dca15dc36efc907410c0a2582299319d2ee` |
| `fixture.py` | `0edd97897e42fe440575383ef21c3b026a94bb339b2d7919c05e58243920e4cc` |
| `mcp_server.py` | `2424778ea57b8a9f8c3ef42b417074801a0ba4ac499c04df01d6a93aeca3cdcd` |
| `native-role.txt` | `15f69b23c897e02f43c30dcb2a117830b18e012f9c321fdb65315682a129dac0` |

Only the Codex canary declares `readOnlyHint=false`, `destructiveHint=false`, and `openWorldHint=false`. Exclusive create-once observation makes its updates additive; existing targets and duplicate calls fail without mutation. Shared non-Codex default program bytes, passive hook bytes, the explicit forbidden-printf rule, native permissions, and global settings remain unchanged. Root-dependent configuration and hook declaration hashes change with the fresh path. The original [packet](native-codex-trust-packet.md), receipts, and default helper binding retain their historical pins.

The six exact candidate user-configuration entries are the project table below and five `[hooks.state."<key>"]` tables. The project value is only `trust_level="trusted"`; each hook value is only its corresponding `trusted_hash`.

```text
projects."/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt4-prepare.fixture"
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt4-prepare.fixture/.codex/hooks.json:session_start:0:0
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt4-prepare.fixture/.codex/hooks.json:pre_tool_use:0:0
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt4-prepare.fixture/.codex/hooks.json:post_tool_use:0:0
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt4-prepare.fixture/.codex/hooks.json:subagent_start:0:0
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt4-prepare.fixture/.codex/hooks.json:subagent_stop:0:0
```

| Hook event | Source-computed declaration hash; native comparison pending |
|---|---|
| SessionStart | `sha256:c4db559651e7f5160654f0539ba01245065dc1cb646f43f20b7403d82b1bc020` |
| PreToolUse | `sha256:5fd023035dc0be87d1c956192e51c6c211d969bc6fd4211c6a8cc8038eddc3dc` |
| PostToolUse | `sha256:c6dc7017f9b85cf66802028280844bceebc0a8fdb37af62b1c046c8625d10c48` |
| SubagentStart | `sha256:ef657018c507d0c8f243d9612e0c2a09be608bd0d643f7d273bf21145cc7f484` |
| SubagentStop | `sha256:cfb71cc0bff905295ece329a10d3c97192a4333f5b63d79342fc07e30375b40c` |

These use the original packet's pinned normalization/fingerprint algorithm, independently cross-checked against its five native-matched hashes. New native `hooks/list` agreement remains mandatory after project enrollment and before hook enrollment. The helper accepts only the two explicitly registered canonical roots, with full aggregate validation; aliases, new roots, changed files, and any preexisting exact records are refused.

Risk & Mitigation: preserve native startup behavior and unresolved runtime-permission metadata. `expectedVersion` plus `reloadUserConfig=false` supplies no final CAS/interprocess lock; unrelated legacy normalization remains possible and must be reported/preserved. Cleanup removes only unchanged whole records created from absence, retains conflicts and uncertain writes, and never restores a complete configuration snapshot. The new scope's current native state remains unobserved until the bounded inventory; explicit requested Sol, inherited effort, and actual reported model remain separate. Genuine MCP/same-child, edit/tests, native follow-up, independent refusal, and cancellation evidence is still required.
