# Runtime verification scope and exit status

Both runtime verifiers require the independent 25-name address contract in
`scripts/verify-market-dependencies.py`. The fixed identity is Ethereum chain 1,
block 19000000, hash
`0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac`.
Address comparison ignores letter case. Chain and block must be integers.

`--manifest PATH` selects an input dependency manifest. The default remains
`forks/aave-v3-dependencies-19000000.json`. Both commands validate its identity,
complete name set, and each address before reading RPC configuration or making
network requests. An invalid manifest exits 1 and preserves an existing output.

For a valid manifest, each target is attempted even after another target fails.
The output preserves target evidence and error rows. It adds `verification_status`,
`execution_commit`, `expected_targets`, and `attempted_targets`. The commit records
Git HEAD at execution. Local uncommitted edits are not represented by that SHA.
Historical checked-in network artifacts are unchanged.

| Exit | `verification_status` | Meaning |
| --- | --- | --- |
| 0 | `exact_full` | All 25 targets have valid full byte equality evidence. |
| 1 | `error` | A target failed or returned an invalid address or result shape. |
| 2 | `partial` | All targets returned valid evidence, but at least one is code-only, substituted, or mismatched. |

Preflight and manifest failures exit 1 before producing a new result. Existing
per-route booleans, statuses, and summary fields remain available. The Source API
route's code-only match now exits 2. Sourcify substitution remains partial evidence.
Neither route treats an unknown result status as partial evidence.

## Issue #51 execution record

1. Investigation. Read Issue #51 and its empty comments, README, SPEC, WORKFLOW,
   both runtime verifiers, dependency identity constants, existing Python tests,
   and `.github/workflows/ci.yml`. The starting commit is
   `1119a92dc70f434648f653563483ef1356a9eac3`. The Source API main returned zero
   unconditionally and silently filtered missing names. Sourcify trusted a
   nonempty mutable target set.
2. Plan. Use the existing dependency helper for the literal address contract and
   manifest validation. Normalize result evidence in the existing runtime module.
   Keep all network artifacts and the separate diagnostics work outside this change.
3. Red and Green. Added `tests/test_runtime_scope.py` first. Running
   `python3 -m unittest discover -s tests -p test_runtime_scope.py` failed with
   34 assertion failures and 15 errors before implementation. These included
   subset and identity acceptance, missing output scope, unconditional success,
   and unsupported `--manifest`. Controlled subprocess runs of the original main
   reproduce exit 0 for an empty Source API manifest, 25 Source API failures, and
   a one-target Sourcify manifest with wrong chain and block. These runs use
   mocked RPC and compilation. The implementation passes the focused suite.
4. Verification. `python3 -m unittest discover -s tests -p 'test*runtime*.py'`
   passes 22 tests. Real CLI subprocess tests reject missing, subset, and wrong
   identity manifests before RPC. Mocked CLI subprocess tests verify complete
   exact, partial, and all-error exit codes. Mixed-result tests preserve successful
   and failed rows and attempt all 25 addresses. Historical artifact scope tests
   compare against the independent literal contract. `python3 -m unittest discover -s tests -v` passes all 89 tests.
   `git diff --check` passes. The CI scope-contract step prints the checked-out
   commit, pinned identity, expected mapping, and validated manifest mapping.
   Running that exact Python snippet locally passes. `actionlint` passes for
   `.github/workflows/ci.yml`. Its output explicitly excludes live runtime proof. Live metadata retrieval and compilation
   are not rerun. Offline tests establish completion semantics, not new network evidence.
5. Review. Independent review found that a Sourcify result could combine
   `exact_full` with an `error` field. Added a regression that failed with exit 0
   instead of 1, then rejected that contradictory result. Unexpected Source API
   exceptions store their type rather than raw exception text. Independent review
   passed after the fix. The unavailable `deslop`
   and `control-cli` tools are not reported as executed. CLI subprocess tests provide
   direct command evidence. The Model the Domain principle places identity in one
   literal mapping. Prove It Works requires the CLI exit and persisted output checks.
6. Delivery. Dedicated branch `issue/51-runtime-scope` targets `main`. PR and CI
   results are recorded in the final delivery report after independent review.
   This change does not merge a PR, close an Issue, or release dependent work.
