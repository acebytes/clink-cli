# Actual tool smoke matrix

All 17 selected tools completed successfully through the real server handler. Chat, clink, all eight expert workflows and both parallel consensus participants invoked the installed, authenticated Codex 0.166.0 using the reviewed v4 direct-catalog policy. Planner, docgen, tracer, challenge, listmodels and version completed locally. The synthetic source file remained byte-for-byte unchanged after every tool.

Every expert result contained actual expert analysis and native backend metadata. Both consensus outcomes were individually successful and valid. The requested model was `gpt-6.1-sol`; the native stream did not report actual provider model identity. The test exercises handler-to-CLI integration; separate offline stdio tests exercise the MCP wire transport.

`apilookup` was deliberately held for the separate [actual search-capability probe](../native-search-1/README.md). Together these records cover all 18 tool outcomes. No real cross-CLI continuation is claimed while other CLI families remain unavailable; deterministic tests cover that protocol.

The result file contains statuses and safe metadata, not raw prompts, generated content, credentials, commands or stderr. The failed first attempt remains in `live-tools-1`; it was not overwritten.
