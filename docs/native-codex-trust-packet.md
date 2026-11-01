# Native Codex trust verification packet

✅ High confidence in the pinned snapshot and observed scoped trust/cleanup results. The user approved the six-entry test after disclosure of native writer normalization and the absence of a final concurrent-write lock. The initial preparation recorded **zero native submissions**; the static tables below preserve that snapshot. Later results do not establish full native admission, resolved effective permissions, or the actual execution model.

Current outcome: [attempt 3](verification/native-codex-0.166.0-attempt3.json) created six previously absent records, observed all five declaration hashes matching native inventory with hooks enabled/trusted, then removed all six unchanged owned records. Cleanup recorded no conflicts, remaining/unconfirmed entries, incidental normalization, or unexpected unrelated changes; unrelated semantic fingerprints matched and control process groups were reaped. The single execution changed the fixture, observed native test failure/success and passive hooks, and passed the independent final test, but ended `permission_needed`. MCP and the named specialist remain unverified; the dedicated forbidden-command check, follow-up, and cancellation were not run. Live checks stopped at **one of four** model submissions, leaving three unused; `native_verified` remains false.

Exact canonical fixture (`F` below):

```text
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt1-prepare.fixture
```

The existing [prepare receipt](verification/native-codex-0.166.0-attempt1-prepare.json) and [preflight](verification/native-codex-0.166.0-attempt1-prepare.preflight.json) record `codex-cli 0.166.0`, executable SHA-256 `39acf979729dd290f9a24ee38b2914e036c91b65952872e28ae2a6b88a7db7c1`, and native recipe `623312bcc2e1f45f831edc1eb4a77598d25460ff0f9a4ce52b0c6c3ec1fbb1ed`. Current fixture digest equals the recorded `c6d7fe36c5c704719d2833318ed37e8d4a2cfa2abaa4bba33e9a8d7bd4067972`; its manifest binds all 12 generated files. Initial snapshot inspection made no native calls or host-configuration reads/edits.

| Prepared snapshot file under `F` | SHA-256 of prepared bytes |
|---|---|
| `.codex/config.toml` | `7417eaf3e862467bdf2a53699c14c5e5df1359af2892713c01ce510a54304de2` |
| `.codex/hooks.json` | `a3b468eb9f3d6105481fc76410ca344085ddf3e02b25d78ceac67d2eea278cac` |
| `codex_hook.py` | `943f26e6fd756b9984749c5fe4b53ecdcbc8ae56a060a8f4f24810200e0f3a42` |
| `.codex/rules/native-fixture.rules` | `5e14c4ce793a0e520faf405ee6119955c5e84e549671c0d7ef2c170404843c08` |
| `.codex/agents/fixture-specialist.toml` | `eeb6c8d906cb5759b03121ae29fd66a8db93c74364a6cd36518a5d972456fbfe` |
| `codex_mcp_server.py` | `2424778ea57b8a9f8c3ef42b417074801a0ba4ac499c04df01d6a93aeca3cdcd` |

The exact project record is `[projects."/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt1-prepare.fixture"]` with `trust_level = "trusted"` in native user configuration. At preparation its existence/value was unknown. Attempt 3 later observed it absent, created it, and observed its removal during cleanup. The scoped enrollment covered only that entry and the five hook records below; no preexisting record was overwritten.

All hook records use `[hooks.state."<exact key>"]` and the corresponding `trusted_hash`. `enabled`, unrelated state, and plugin `trusted_content_hash` remain outside the expected changes. Exact keys, in table order:

```text
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt1-prepare.fixture/.codex/hooks.json:session_start:0:0
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt1-prepare.fixture/.codex/hooks.json:pre_tool_use:0:0
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt1-prepare.fixture/.codex/hooks.json:post_tool_use:0:0
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt1-prepare.fixture/.codex/hooks.json:subagent_start:0:0
/Users/d/Tools/clink-mcp-server/test_output/native-adapters/native-codex-0.166.0-attempt1-prepare.fixture/.codex/hooks.json:subagent_stop:0:0
```

| Event | Initial source-computed hash; native agreement observed in attempt 3 |
|---|---|
| SessionStart | `sha256:9482eed34b1b9a959850d28ca3a05e784ebba9c04f1a9fe26353ee44fb106078` |
| PreToolUse | `sha256:37d003d178723374fd0153f1eb417f430faf9ed26a4910a880484d0af8c8b827` |
| PostToolUse | `sha256:086d522ad2f2ee0a6fe0578146529008bf5440f8a211ae9a590257281dfcdf55` |
| SubagentStart | `sha256:10dcd57fad2efbd7a7878861b49b276b258b00f3f76bd54c4263729554cfb1b8` |
| SubagentStop | `sha256:ae582946138d8e3cb02829c07096c2f26f182bed3d616a395c7bd6bb89005177` |

Hash computation follows [pinned discovery/normalization](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/hooks/src/engine/discovery.rs#L650-L713), [identity hashing](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/hooks/src/engine/discovery.rs#L972-L998), and [sorted JSON fingerprinting](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/config/src/fingerprint.rs#L54-L65). For this snapshot only: preserve each matcher and command, keep `timeout: 5`, add `async: false`, omit absent optional fields, and use snake-case `event_name`. SHA-256 covers compact recursively key-sorted UTF-8 JSON of `{event_name, matcher, hooks: [handler]}`. Python `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()` reproduces these bytes. The pinned [official test vector](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/config/src/fingerprint_tests.rs) was independently reproduced (`sha256:a5e4c8fbdbc75d185b0f2b5291bfb2de10e8c523d6c89248876fd78a97c83176`). The later native `hooks/list` comparison passed for all five hashes in attempt 3. Local script contents are **not** part of native declaration hashing; the file digest above independently pins them.

Pinned native permission semantics: plain `exec` initially selects headless approval `Never`, rebuilding without that override when the resolved reviewer is `AutoReview`. Without a sandbox flag, native configuration resolves legacy defaults or named profiles, platform behavior, and managed constraints; disallowed candidates fall back to a compliant native value or fail. The wrapper adds no permission override. Null configured approval/reviewer/sandbox fields remain unresolved metadata and alone do not prohibit this authorized test. [Exec resolution](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/exec/src/lib.rs#L592-L637), [AutoReview](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/exec/src/lib.rs#L785-L817), [constraint enforcement](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/config/mod.rs#L4345-L4397).

Supplementary `configRequirements/read` accepts omitted/null parameters and reads merged managed constraints without creating a thread or dispatching model/hooks. Its allowed sets and profile constraints do not establish final fixture-specific runtime permissions. Preserve that distinction and require the genuine forbidden-command refusal. [Read route](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/app-server-protocol/src/protocol/common.rs#L1520-L1525), [handler](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/app-server/src/request_processors/config_processor.rs#L140-L156).

Risk & Mitigation: the remaining gate includes these concrete limits, not a claim that one enrollment route is safer:

- Trust can change implicit approval/sandbox defaults; effective permissions before/after are unknown. Attempt 3's unchanged configured fields do not establish equal resolved runtime permissions. [Approval defaults](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/config/mod.rs#L3962-L3971), [sandbox defaults](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/config/src/config_toml.rs#L862-L871)
- Native `expectedVersion` checks a loaded semantic snapshot before a separate reread/edit/atomic replacement; no final CAS/interprocess lock is present. Unrelated concurrent writes can be lost. A fixture-only lock does not coordinate native writers. [Write path](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/config/edit.rs#L749-L791)
- Native batch writes can normalize unrelated legacy plugin hook hashes; `reloadUserConfig: true` refreshes all loaded threads. Inventory also includes inherited hook commands, requiring private filtering before any export. [Normalization](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/app-server/src/config_manager_service.rs#L431-L458), [reload](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/app-server/src/request_processors/config_processor.rs#L379-L442)
- Cleanup must compare owned records with exactly what was inserted and preserve unrelated/current state. If the project entry was originally absent, removing only `trust_level` leaves an empty entry with different lookup semantics; remove the unchanged owned entry itself. Existing values/conflicts require explicit handling. Store cleanup cannot retract settings/history already loaded by tasks. [Empty-entry precedence](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/config/src/project_trust.rs#L16-L94)

Remaining lifecycle evidence includes the exact forbidden `printf` refusal, configured MCP environment canary, same-child specialist result, same-session follow-up, and cancellation cleanup. Observed trust, edit/tests, passive hooks, and trust-store cleanup do not substitute for those checks or establish native admission. Effective permissions and the actual model remain unverified. No further live calls are planned.
