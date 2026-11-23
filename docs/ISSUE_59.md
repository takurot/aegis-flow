# Issue 59 metadata recovery record

Issue #59 seeks full runtime reproduction for the PayloadsController proxy,
PayloadsController implementation, and Level1 Executor at Ethereum block
19000000. Stage 1 remains `BLOCKED`. The original deployment metadata or
compiler input has not been recovered. The candidate evidence in
[`forks/metadata-recovery/payloads`](../forks/metadata-recovery/payloads/)
records failed builds and source provenance. It does not satisfy the Issue's
full-byte acceptance conditions.

## Targets and required artifacts

The artifact names `governance_proxy` and `governance_impl` refer to
PayloadsController. They do not refer to the root Governance contract.
The fixed scope is chain 1, block 19000000, hash
`0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac`.

| Target | Address | Compiler in deployed CBOR | Required metadata CID |
| --- | --- | --- | --- |
| PayloadsController proxy | `0xdabad81af85554e9ae636395611c58f7ec1aaec5` | 0.8.10 | `QmNRkeSjuybfntB3kU2HGJ187i2FWZfS5rn1bdC6HLL61B` |
| PayloadsController implementation | `0x7222182cb9c5320587b5148bf03eee107ad64578` | 0.8.19 | `QmWpzXgE8u4TQHmN3ob2FUdUict4ozTCBAYqnkrmvx1y9F` |
| Level1 Executor | `0x5300a1a15135ea4dc7ad5a167152c01efc9b192a` | 0.8.19 | `QmPyBHeMtRkSEHgdV7oSGbzwd7hLpkLkAmG5VjQkGfd6X2` |

The saved deployment snapshot comes from the initial public governance commit
[`7afbf3134ba778da0418b3ce2e80668e17dd5a75`](https://github.com/aave-dao/aave-governance-v3/tree/7afbf3134ba778da0418b3ce2e80668e17dd5a75),
dated 2023-09-28. The recursively pinned submodule status is retained. This
snapshot lists all three addresses but does not authenticate the deployment
build. Its configured compiler 0.8.14 does not override the deployed CBOR.
The CBOR identifies version numbers, not a complete original toolchain.

## Source and build findings

The [source comparison](../forks/metadata-recovery/payloads/official-source-comparison.json)
uses SHA256 of UTF-8 source bytes. All six proxy sources match pinned
solidity-utils commit `56720adb8b751c3742a5f9b8c5bcb8a9446ba941`.
All five Executor sources match the pinned public graph. The controller
candidate metadata declares 20 sources. Sixteen match at their original paths.
A seventeenth has equal content at the renamed delivery-infrastructure path.
`SafeERC20.sol` and `IPayloadsController.sol` differ. The old
`draft-IERC20Permit.sol` is absent from the public snapshot. The renamed
`IERC20Permit.sol` also has different content.

The saved candidate sources retain their original comments and licenses.
The source-associated bundles authenticate those candidate contents and the
approved compiler tree. Candidate authentication does not establish that the
candidate was the deployment input.

The [candidate ledger](../forks/metadata-recovery/payloads/candidates.json)
contains six final remapping experiments and the original controller explorer
baseline. Each build was replayed with Issue #53's independently pinned
compiler. Every emitted metadata string matches the saved candidate metadata
bytes. No emitted CID matches its deployed CID. All six proxy and Executor
experiments match code after metadata removal. That diagnostic comparison is
not acceptance. The controller baseline also differs outside metadata because
its immutable values have not been independently bound. Neither the metadata
nor the constructor acceptance condition has passed.

The publication remappings were generated with Foundry 1.8.6. A deployment-era
Foundry binary was not recovered. The older global graph and the pre-rename
graph are inferred candidates. Preliminary graphs from an incomplete recursive
checkout are excluded from the final ledger.

The official npm package `@bgd-labs/solidity-utils@1.1.0` supplies source, not
original build-info. Four of its six proxy sources match the candidate.
The other two differ. The retained release inventories list no governance
releases and two solidity-utils releases from 2025 with no attached assets.
These bounded observations do not prove that no other build artifact exists.
Gateway retrieval failures do not prove global IPFS absence.

## Verification and workflow state

1. Investigation checked Issue #59 and its empty comments, WORKFLOW, SPEC,
   README, saved fixed-block runtime, deployment history, and candidate inputs.
   The worktree branches from verified Issue #53 commit `0638ead`.
2. The feasibility gate failed because original deployment metadata is missing.
   This change records evidence only. It adds no runtime decoder or target
   success result and does not unlock dependent implementation stages.
3. Red and Green are not applicable to this evidence-only change. Actual
   compiler replay, source comparisons, full-runtime mismatches, artifact hash
   verification, and documentation review replace mirrored unit tests.
4. Seven authenticated local replays reproduce the candidate metadata bytes.
   Their emitted CIDs differ from the deployed CIDs. The SHA256 manifest covers
   every retained evidence file except the manifest itself. Hashes in the ledger
   explicitly distinguish raw runtime bytes from UTF-8 JSON bytes.
5. Independent parent review passed. The reviewer replayed all seven bundles,
   checked saved metadata bytes, CID mismatches, code-only diagnostics, artifact
   hashes, and local documentation links. No actionable comment finding remained.
   Exact-head CI is recorded in the delivery report. These checks review the
   evidence change and do not pass the target implementation gate.
6. The documentation PR references #59 without closing it. No merge, Issue
   closure, governance action, upgrade, or public-chain write is performed.

The Prove It Works principle required actual compiler output comparisons.
It kept every failed CID visible rather than treating code-only equality as
full runtime acceptance. The technical-writing reference format keeps the
failed gate separate from the recovery procedure.

## Reopen the feasibility gate

Obtain either the raw metadata JSON matching each CID above or original
standard JSON compiler input and build-info with deployment-time source bytes,
settings, remappings, compiler identity, and submodule revisions. The controller
requires the pre-public build before the crosschain-to-delivery path rename.
The operator reported no known location for those artifacts.

After recovering the input, authenticate it with Issue #53's bundle machinery.
Then derive the controller's three constructor values from authenticated
creation evidence and bind them to the source AST using Issue #54's helpers.
The controller creation transaction is
`0x821bb6835c3616bdba494a874b0e498f23c0281911bb3a47c3adf0a62305cadc`.
Fixed-block getters alone do not establish constructor provenance.
Each target still requires an independent full-byte comparison and negative
cases for wrong chain, message originator, controller, and metadata.

To reproduce a saved candidate, use the bundle path in `candidates.json`.
The approved compiler installation must already be present and authenticated.
The replay operation does not fetch compiler files or source.

```sh
PATH=/path/to/foundry:$PATH python3 scripts/runtime-build-inputs.py validate forks/metadata-recovery/payloads/bundles/<sha256>.json
PATH=/path/to/foundry:$PATH python3 scripts/runtime-build-inputs.py replay forks/metadata-recovery/payloads/bundles/<sha256>.json --output /tmp/payloads-candidate-output.json
```

The replay command succeeding establishes that the candidate compiles. The
ledger's `full_runtime_equal` remains `false` for every saved candidate.
