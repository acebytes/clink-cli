# clink-mcp-server documentation

Current CLI-only setup and operation:

| Document | Description |
| --- | --- |
| [Getting started](getting-started.md) | Explicit setup, stdio client connection, discovery, and first consultation. |
| [CLI configuration](cli-configuration.md) | Strict manifests, native models, whole-client precedence, roots, roles, and execution bounds. |
| [Migration from PAL](migration.md) | Changed configuration priority, removed provider routing, and continuation/launcher changes. |
| [Codex/Claude policy verification](verification/cli-policy/README.md) | Version-bound Codex policy evidence, current admission status, preserved attempts, verified structured search, and Claude limitations. |
| [Antigravity/Grok admission investigation](clink-cli-admission.md) | Installed identities and unresolved policy gates for these two unavailable CLIs. |
| [Docker deployment](docker-deployment.md) | Stdio container packaging and native CLI limitations. |
| [Implementation results](clink-implementation-results.md) | Integrated verification, preserved failures, and current delivery limits. |
| [Development guide](../CLAUDE.md) | Current test commands, code map, diagnostics, and implementation guardrails. |

## Historical provenance

The [PAL audit](clink-only-audit.md), [baseline](clink-only-baseline.md), and [review](clink-only-review.md) describe the pinned upstream source and preimplementation evidence. The [approved design](clink-only-design.md) and [implementation plan](clink-implementation-plan.md) record the migration's intended contracts. They do not replace current test/admission results.

Retained upstream guides, including [provider configuration](configuration.md), [adding providers](adding_providers.md), [Azure OpenAI](azure_openai.md), [custom models](custom_models.md), [model ranking](model_ranking.md), [advanced usage](advanced-usage.md), and the older [tool reference](tools/), describe former PAL behavior. Provider credentials, automatic routing, permission flags, setup commands, and capability claims in those historical guides are not instructions for this fork. Use the current guides above and the server's tool schemas.

Derived from PAL MCP Server, formerly Zen MCP. Copyright 2025 Beehive Innovations; [Apache 2.0 license](../LICENSE).
