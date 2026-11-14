# Issue 53 execution record

1. Investigation. Read the live Issue 53 and its empty comments, WORKFLOW, SPEC,
   README, the Issue 51 target contract, and Issue 52 diagnostics. The worktree
   starts at verified Issue 52 commit `6825cae`. Source content lacked metadata
   Keccak checks. Compiler selection trusted an installed path and could copy
   an unrecorded `common` fallback. Neither runtime caller retained a complete
   replay input. Historical target results do not establish current input trust.
2. Plan. Own one shared compiler-input module, its independent compiler pins,
   and both runtime callers. Keep a JSON bundle with exact input and metadata
   bytes, fixed-block identity, compilation target, and explicit metadata origin.
   Keep source paths as keys. Validate source association and authenticate
   compiler files before execution. The parent compared two designs before this
   implementation. An archive service and package resolver remain outside scope.
   Blocking first steps were the Issue 51 and 52 contracts. This slice has one
   owner because the module and both consumers share the same invariant.
3. Red and Green. Added `tests/test_runtime_build_inputs.py` first. The first
   run failed because the shared module did not exist. Implemented the narrow
   module, then tested raw-byte round trips, absolute source keys, empty library
   settings, wrong source content, missing sources, malformed hashes, SHA3
   substitution, missing blobs, extra source labeling, wrong versions, forged
   bundle pins, substituted soljson, wrappers, and common helpers. Independent
   parent pre-review identified a missing external pin for the local driver and
   a missing compilationTarget constraint. Both now have explicit validation
   and regression coverage. No new code comments were added.
4. Verification. On Python 3.14.7, the focused suite passes 12 tests with
   `/tmp/aegis-foundry-1.8.6` on PATH. The known empty-string Keccak vector uses
   actual `cast`. A real approved 0.8.19 compiler compiles the fixture while
   Python network acquisition is patched to fail. The actual Source API and
   Sourcify runtime callers write validated bundles during transport-mocked
   integration tests. Synthetic RPC credentials and NODE_OPTIONS are absent
   from the bundle and compiler environment.

   `PATH=/tmp/aegis-foundry-1.8.6:$PATH python3 -m unittest discover -s tests -v`
   passes all 110 tests. Without the Foundry PATH, the command runs 110 tests,
   passes 102 tests, and skips eight tests that need the optional Keccak backend. Compiler integrity
   negative tests still run without that backend. `actionlint .github/workflows/ci.yml`
   and `git diff --check` pass. CI adds a genuine Keccak and compiler replay
   step to the Foundry-equipped Solidity job. Its exact nine-package compiler
   installation was reproduced from an empty local directory and passed the
   independent file pins. All seven existing local compiler installations
   passed the full-version and file-manifest check.

   Actual offline replays used the unchanged modern acquisition files for
   Configurator 0.8.10 and USDC 0.6.12. Both kept `libraries={}`. All 22 and 23
   metadata-declared sources passed real Keccak association. Each replayed
   contract metadata string equals the supplied compiler-generated metadata
   bytes. The CLI replay also passed for the Configurator bundle. Local proof
   is `/tmp/issue53/replay-proof.json`. These raw-byte digests identify the data.

   | Target | Input SHA256 | Metadata SHA256 |
   | --- | --- | --- |
   | configurator_impl | `3f8fe71e78130fe97751408ddd7246161578347b6062a75510211ef7dd56bac5` | `7b7034db8f1c6ebf8d49af834c601ca1848f240fee9ec0739a19d0208aae86c1` |
   | USDC_impl | `d28873e861606573902300c0f6713648828dbb19e6918f702fb5aa972eb1ff93` | `18925db7e2746c4ec4409a152c64ba24ecec7f01e0c23636a806fb2ba23d2a9f` |

   No live RPC or credential configuration was used. No historical runtime
   artifact was regenerated. Compiler-input validation is separate from
   deployment CID equality, independent link or immutable binding, and full
   fixed-block runtime equality. Those downstream checks remain required.
5. Review. Fresh independent review passed. The reviewer reran all 110 tests,
   actionlint, and diff checks. Independent acquisition of all 85 registry
   tarballs reproduced every SRI and all 1,154 file pins. The reviewer also
   replayed both actual inputs and verified exact input and metadata bytes.
   No P1 or P2 finding remained. The separate no-comments review found zero
   actionable flags and required zero comment deletions. Nested agents are
   prohibited for this implementation owner. The pstack Model the Domain and Prove It Works principles guided the
   shared input contract and actual compiler replay. The parent owns the
   separate no-comments review. Unavailable model defaults, deslop, and
   control-cli are not reported as executed.
6. Delivery. The branch `issue/53-build-input-provenance` targets
   `issue/52-safe-diagnostics`. Commit, push, PR creation, and terminal CI are
   recorded in the delivery report after parent approval. This record does not
   claim merge, Issue closure,
   complete runtime coverage, or any market-protection acceptance.
