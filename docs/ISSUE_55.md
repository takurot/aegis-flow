# Issue 55 remains blocked on original legacy metadata

[Issue #55](https://github.com/takurot/aegis-flow/issues/55) requires every runtime byte of WETH and the USDC proxy at Ethereum block `19000000`. The original metadata preimages and deployment build records have not been recovered. The user confirmed that they do not know a location for those records. Stage 1 remains **BLOCKED**.

This evidence change preserves the investigation for the next owner. It does not implement the typed metadata decoder, satisfy Issue #55, or release dependent full-runtime verification gates. [SPEC](SPEC.md) requires reproducible evidence and explicit missing-data states. [WORKFLOW](WORKFLOW.md) forbids treating an unmet feasibility gate as success.

## Recorded evidence

The [legacy evidence directory](../forks/metadata-recovery/legacy/README.md) contains source-associated compiler inputs, candidate metadata, runtime snapshots, retrieval observations, and a bounded candidate ledger. Source content, comments, licenses, source paths, and settings remain intact in the bundles.

The two primary candidates were captured and replayed with the existing Issue #53 tools and approved compiler pins. WETH uses solc `0.4.19+commit.c4cbbb05`. The Circle historical five-source proxy uses `0.4.24+commit.e67f0147`. Both replays produce the saved candidate metadata exactly. Both match execution code and fail full-runtime equality against the saved pinned runtime. These are candidate build inputs, not authenticated original deployment metadata.

The acquisition ledger records 38 compiled candidates with matching execution code and different metadata. Five attempts with the historical contract name `WETH9_` did not produce the requested `WETH9` target and do not enter that count. Ten of the 38 use experimental solc 0.4.18, which has no current approved compiler pin. Those experiments do not authenticate either 0.4.18 or the explorer-reported 0.4.19 as the original compiler. The legacy CBOR reference itself contains no compiler version.

Gateway HTTP 404 observations apply to the recorded URLs and request attempts. They do not prove global data loss. The failed 64-character `bzz.link` DNS-label experiment made no network request.

## Six-stage status

| Stage | Issue #55 status | Evidence change status |
| --- | --- | --- |
| 1. Investigation and feasibility | BLOCKED. Original metadata and deployment build identity are unavailable. | Live Issue and repository contracts checked. Negative evidence retained. |
| 2. Plan and environment | Target implementation cannot proceed past Stage 1. | Isolated `issue/55-legacy-metadata-evidence` worktree based on Issue #53 commit `0638eaddc0d32e37a7c0206c741d8398497f47c7`. |
| 3. Red, Green, Refactor | Not passed. No decoder or recovery implementation. | Documentation and public-input preservation only. No mirrored static-document unit tests. |
| 4. Verification | Not passed. Full-runtime mismatch and original-input authentication remain unresolved. | Two actual offline compiler replays, candidate metadata comparison, hash manifest verification, local-link check, and whitespace check pass. |
| 5. Independent review | Not passed for the target implementation. | Root review independently verified both replays, metadata bytes, runtime mismatches, manifest hashes, and local links. The raw-byte preservation update passed follow-up hash and whitespace checks. |
| 6. Delivery | Issue acceptance remains unmet. No merge or close is requested. | Evidence PR references #55 without a closing keyword. PR CI does not prove target runtime equality. |

## Restart condition

The smallest handoff is the original metadata JSON preimage for either deployed `bzzr0` reference, or a deployment-specific build artifact with exact source bytes, paths, settings, and compiler identity. An authorized public copy or a location for that artifact is enough to restart acquisition. Credentials are unnecessary.

For Circle, the historical build configuration archives `build/contracts/*` to a private CodeBuild S3 artifact bucket. A retained `FiatTokenProxy.json` is a concrete lead. That template does not prove that a deployment build ran, that artifacts survive, or that the historical commit was deployed. No private-bucket access was attempted.

After acquisition, establish deployment provenance, verify the original legacy hash, and rebuild both complete runtimes. Then implement typed `ipfs`, `bzzr0`, `bzzr1`, and `none` handling and the required unavailable-reference and modified-source negative cases. Metadata copying, removal, and masking cannot satisfy acceptance. The USDC implementation remains the separate target in #58.
