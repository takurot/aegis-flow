# Secret-free verifier diagnostics

The fixed-block verifiers treat RPC errors, Source API error bodies, configuration
parser diagnostics, and transport exceptions as untrusted input. Operators receive
fixed failure categories. Logs and artifacts omit remote error messages, URL hosts,
paths, query tokens, API keys, and exception chains.

The shared RPC helper accepts an error only when the envelope has a numeric request
ID, the error code is an integer, the message is a string, and no result accompanies
the error. Booleans are not integer IDs or codes. Valid numeric error codes remain
visible. Other error shapes produce a fixed invalid-envelope diagnostic. Source API
string results become `source API unsuccessful`; their contents are not retained.

Configuration read and parse failures become `invalid configuration file`. Invalid
URL parsing becomes `invalid HTTPS RPC endpoint`. RPC and Source API transport
failures use fixed categories. Source HTTP failures retain only numeric status.
The dependency and legacy runtime preflight commands exit 1 with fixed messages.
Legacy runtime target exceptions become `runtime target failure`, and verification
continues through all 25 expected targets before returning the existing error exit.
Governance `cast` timeout and OS errors become `cast execution failed` without the
command or its RPC URL.

## Issue #52 execution record

1. Investigation. Read Issue #52 and its empty comments, WORKFLOW, SPEC §5.3,
   README, the shared helper and all importing verifier scripts, and the existing
   runtime and Sourcify tests. The dedicated branch starts at Issue #51 commit
   `2835b05ca3cb599b11fd681fa14caf9faf434c54`. RPC error codes and Source API string
   results were copied directly into diagnostics. Legacy runtime preflight and
   target exceptions rendered arbitrary Failure text. The governance subprocess
   timeout could also render the command containing `--rpc-url`.
2. Plan. Keep the existing helper and CLI boundaries. Validate RPC error shapes,
   replace unsafe error text with fixed categories, and suppress parser and tool
   exception chains. Use synthetic markers without credentials or external calls.
   Preserve Issue #51 identity, 25-target scope, provenance, and exit semantics.
3. Red and Green. Added `tests/test_safe_diagnostics.py` before implementation.
   The initial run produced 23 failures for string and object codes, bool and null
   codes, Source API error bodies, preflight exceptions, and target diagnostics.
   A separate transport regression reproduced a raw ValueError from `urlopen`.
   A process regression reproduced the governance timeout command leak. Each was
   fixed at the existing boundary and rerun. The tests inspect both output streams,
   serialized failure artifacts, and rendered exception chains.
4. Verification. Focused diagnostics tests exercise synthetic host, path, query,
   and API-key markers, invalid JSON, malformed configuration, transport errors,
   tool timeouts, and all-target failure output. Actual CLI subprocesses reject
   malformed configuration without tracebacks or output artifacts. Mocked CLI
   subprocesses verify nonzero exit and all 25 attempted addresses on target
   failure. Existing runtime, Sourcify redaction, and normalization tests remain
   unchanged. On Python 3.14.7, the focused diagnostic suite passes 9 tests,
   the runtime suite passes 22 tests, and `python3 -m unittest discover -s tests -v`
   passes all 98 tests. `git diff --check` passes.
   No live RPC, source download, compiler, credential file, Rust, or Solidity gate
   is required to reproduce this Python diagnostic change. CI remains a separate
   delivery requirement. No new live-runtime evidence is claimed.
5. Review. Independent review reproduced a dependency-discovery ValueError
   that echoed a malformed RPC numeric result. Added a failing process regression
   and decoder regressions before adding hexadecimal validation to the shared
   address, integer, bytecode, payload, executor, and role-event boundaries.
   Fresh independent review passed after the fix and reran all 9 diagnostics
   tests and 98 Python tests. Parent review of helper boundaries and governance
   timeout handling passed. No new code comments were added.
   The Prove It Works principle requires subprocess output and persisted artifact
   assertions. The secret-safe-parser-diagnostics skill requires generic public
   errors and suppressed raw exception chains. The unavailable `deslop` and
   `control-cli` tools are not reported as executed.
6. Delivery. The branch `issue/52-safe-diagnostics` targets
   `issue/51-runtime-scope`, so its PR contains only Issue #52 changes. Commit,
   push, PR, and terminal CI state are recorded in the delivery report.
   Independent review passed before commit.
   This work does not merge a PR, close an Issue, change credentials, or enable
   any market integration.
