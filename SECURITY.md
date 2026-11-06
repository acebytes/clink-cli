# Security policy

This policy covers [clink-mcp-server](https://github.com/acebytes/clink-mcp-server), a CLI-only fork of PAL MCP Server. Security fixes target the current 0.2.x release line. Historical upstream PAL 9.x versions are not releases of this fork.

## Report a vulnerability privately

Do not publish exploit details, credentials, private prompts, user records, or raw native sessions in an issue. Use this repository's [private vulnerability reporting](https://github.com/acebytes/clink-mcp-server/security/advisories/new) when available. If GitHub private reporting is unavailable, contact the repository maintainer through their public profile to arrange a private channel before sharing sensitive details. Do not send fork-specific reports to the upstream PAL project.

Include the affected clink version and operation (consultation, native task, generation, setup, or local discovery), operating system, Python and CLI versions, sanitized reproduction steps, expected and observed behavior, and potential impact. Use synthetic files and redact sensitive values. No response-time commitment is made.

## Execution and data boundaries

- Native CLIs own authentication and native permission decisions. Clink does not require hosted-provider API keys, copy login stores, grant native trust, or provide an approval bypass or relay.
- Consultation, native tasks, and programmatic generation have separate admissions. A valid version string, executable installation, or successful login is insufficient; enabled operations require their pinned binary and execution bindings. Changed versions or recipes fail closed pending verification.
- Consultations return advice and proposed edits. Explicit native tasks and native generation retain the CLI's configured capabilities and can change files or external systems. Approved roots constrain project selection and supplied context; they do not sandbox every native tool or provide OS read isolation.
- Native execution can produce partial effects before failure, timeout, or cancellation. Cancellation does not roll back changes or guarantee detached descendants and remote jobs stopped. A completed result validates execution/final-output transport, not fulfillment. Independently inspect effects and run checks.
- Project manifests can select executables, roots, role guidance, and inherited environment names. Load repository configuration deliberately through explicit `--project` binding and the host's trust controls. Generation loads its operator configuration independently of request paths.
- Native tasks, consultation continuations, session mappings, and deduplication are held in memory. Restart loses that state. Save useful results and inspect ambiguous outcomes before explicitly resubmitting work.

## Protect local configuration and reports

Keep secrets in native CLI authentication stores or documented environment/configuration locations. Do not commit `.env`, credentials, private consumer configuration, runtime logs, or raw model/session output. Inherit only the environment variable names needed for integrations; names belong in manifests, while values remain outside tracked source.

Use disposable projects for live checks and review generated changes before using them. Keep dependency updates and native CLI upgrades deliberate: package installation is separate from ordinary server startup, and a CLI upgrade may require new admission evidence. The base [container](docker/README.md) has no CLI binaries or authentication files and is intended for provider-free startup, discovery, and local tools.

Security reports in scope include command injection, admission or path-boundary bypass, private-file cleanup failures, sensitive-data disclosure, exploitable dependency issues, and denial of service in the server. Report defects in external CLIs, MCP clients, or third-party services to their maintainers as appropriate; describe any clink boundary failure separately.

Distributed under [Apache 2.0](LICENSE), including its warranty disclaimer. Derived from PAL MCP Server; Copyright 2025 Beehive Innovations.
