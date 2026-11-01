# Codex runtime resource attribution

`codex-0.166.0-models-direct.json` is a complete bundled `ModelsResponse` captured without refresh or inference from the installed `codex-cli 0.166.0` binary. It contains twelve native model records, preserving their identifiers, order, default metadata and native prompts. This is a version-pinned CLI compatibility resource rather than a hosted-provider catalog or a dynamic model-discovery service.

The fork modified only each record's `tool_mode` field to `direct`. This allows the verified Codex recipe to keep execution/delegation features disabled while preserving the native model records. The remaining fields are unchanged. No account metadata or credentials were captured. Updating the executable, recipe or this asset requires renewed admission; the wrapper checks the resource hash before launch.

- Projected resource: 757,141 bytes; SHA-256 `69d3e68d0e78a684c56b1818f1351149902fc61c398e4b69d371dfdea08e4e12`.
- Original bundled response: 745,260 bytes; SHA-256 `9e01cd50ec8c4ba6d782413d1154cb2dbe1da63e014d622631b0603bc19659d6`.
- Captured executable SHA-256: `39acf979729dd290f9a24ee38b2914e036c91b65952872e28ae2a6b88a7db7c1`.
- Capture provenance and original response are retained in the source repository under `docs/verification/cli-policy/`.

OpenAI Codex is copyright 2025 OpenAI. The upstream Apache 2.0 license and attribution notice are retained as `CODEX-LICENSE` and `CODEX-NOTICE`. They were retrieved from the pinned upstream source commit [`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`](https://github.com/openai/codex/tree/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856): [LICENSE](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/LICENSE), [NOTICE](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/NOTICE). Existing PAL/Beehive notices remain in the repository's root license and README.
