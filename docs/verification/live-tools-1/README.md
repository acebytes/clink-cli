# Initial real-tool smoke attempt

This opt-in run used the admitted Codex 0.166.0 v2 recipe with synthetic source and requested model `gpt-6.1-sol`. The CLI completed four consultations, but the strict production parser rejected their initial error records. Chat, clink, thinkdeep and codereview therefore returned `parse_error`; the synthetic file remained unchanged throughout.

The conductor interrupted the loop rather than repeat the same failure across the remaining tools. Precommit was active when SIGINT cancelled the Python task; the runner reported `outcome=cancelled` and completed cleanup. The harness exited 130. That interrupted call is not a successful test and is not in the completed-results JSON.

Diagnosis found native configuration warnings encoded as error events before successful terminal events. The parser's rejection was retained; a corrected, independently verified launch recipe is required before another smoke run. No backend retry or fallback occurred. Later evidence must use a separate directory and preserve this failed attempt.
