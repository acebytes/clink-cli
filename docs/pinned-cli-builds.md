# Get a supported CLI build on Apple Silicon

Clink 0.2.3 checks the SHA-256 of the **executable** and the launch configuration for each operation. These are first-party downloads for the four recorded macOS arm64 builds. Each executable below was retrieved from the linked download, unpacked if necessary, and matched against Clink’s recorded fingerprint. A version label alone can identify several different binaries.

| CLI | Where to get the exact build | Executable to check |
| --- | --- | --- |
| Codex 0.166.0 | On [OpenAI’s 0.166.0 release](https://github.com/openai/codex/releases/tag/rust-v0.166.0), choose [codex-package-aarch64-apple-darwin.tar.gz](https://github.com/openai/codex/releases/download/rust-v0.166.0/codex-package-aarch64-apple-darwin.tar.gz). | Extract the package into its own directory and keep its layout. Check `bin/codex`. |
| Claude Code 2.1.304 | Download Anthropic’s [darwin-arm64 executable](https://downloads.claude.ai/claude-code-releases/2.1.304/darwin-arm64/claude). Its digest is also published in the [2.1.304 manifest](https://downloads.claude.ai/claude-code-releases/2.1.304/manifest.json). | Save it as `claude`. |
| xAI Grok Build 1.0.70 | Download xAI’s [grok-1.0.70-macos-aarch64](https://x.ai/cli/grok-1.0.70-macos-aarch64). | Save it as `grok`. |
| Antigravity 1.4.12 | Download Google’s [cli_mac_arm64.tar.gz, build 4768735748292608](https://storage.googleapis.com/antigravity-public/antigravity-cli/1.4.12-4768735748292608/darwin-arm/cli_mac_arm64.tar.gz). | Extract `antigravity`. Check that file, then give it the command name `agy`. This build supports MCP native tasks only. |

## Verify and select the executable

Keep a pinned build in a separate versioned directory if you also use the vendor’s current CLI. Run `shasum -a 256 /absolute/path/to/executable` on the unpacked file. Compare all 64 hexadecimal characters against the corresponding value:

| Executable | SHA-256 |
| --- | --- |
| `codex` | `39acf979729dd290f9a24ee38b2914e036c91b65952872e28ae2a6b88a7db7c1` |
| `claude` | `b9ac5374839f14abf3f1f45610637c332b5daee5ce0d1634726cef87aab78e6a` |
| `grok` | `5c8a688251bf61e35370b828b6a353b0a20fcc3f64000de8a91d426039f29cec` |
| `agy` | `34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3` |

For example, these macOS commands download Claude Code alongside an existing installation, verify the bytes **before** making the file executable, and check Clink compatibility. Run them with `clink` already installed:

```sh
(
  set -eu
  dir="$HOME/.local/opt/clink-agents/claude-2.1.304"
  mkdir -p "$dir"
  cd "$dir"
  file="$(mktemp claude.download.XXXXXX)"
  trap 'rm -f "$file"' EXIT
  curl --fail --location --silent --show-error \
    https://downloads.claude.ai/claude-code-releases/2.1.304/darwin-arm64/claude -o "$file"
  printf '%s  %s\n' b9ac5374839f14abf3f1f45610637c332b5daee5ce0d1634726cef87aab78e6a "$file" | shasum -a 256 -c -
  chmod 755 "$file"
  mv -f "$file" claude
  clink generation-capabilities --json --cli claude --executable "$(pwd -P)/claude"
)
```

For the other standalone binary, give the verified `grok` executable permission with `chmod 755 grok`. For Google’s archive, rename the extracted executable `agy` in your versioned directory. To use any of these builds, put its executable directory on the calling process’s `PATH`, or set the effective agent manifest’s `command` to the absolute executable path. Desktop clients can have a different `PATH` from your terminal. A preflight with `--executable` checks that file once. It does not change which executable later requests select. [CLI configuration](cli-configuration.md)

For Codex, Claude or Grok generation, run `clink generation-capabilities --json --cli NAME --executable /absolute/path/to/file`. Look for `preflight.status` equal to `compatible`. The check reads the executable without starting it. Sign in separately using the vendor CLI before your first request. For Agy, inspect `native_task_clients` in the MCP `listmodels` result. Agy generation reports `admission_unverified` even when the fingerprint matches.

Compare the [supported operations](../README.md#supported-operations) before choosing a build. Updating the executable can change its fingerprint and requires fresh verification for that operation.
