# Issue 57 original Chainlink metadata gate

[Issue 57](https://github.com/takurot/aegis-flow/issues/57) requires complete deployed runtimes for the WETH price proxy and USDC price aggregator. Stage 1 remains `BLOCKED`. The saved candidates reproduce their own metadata, but neither reproduces its deployment CID. This evidence does not complete Issue 57 or parent Issue 44.

The scope is Ethereum chain 1 at block `19000000`, hash `0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac`.

| Target | Address | Required deployed CID | Replayed candidate CID |
| --- | --- | --- | --- |
| WETH price proxy | `0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419` | `QmR48zpR684GHSuELCcbjBLC6yh1Pic5kduNusFNwGPb7j` | `QmTfVcpJkTdLDRwjdQrxpAmnRVuZf8nnbLJhcQKxp6Q54c` |
| USDC price aggregator | `0x789190466e21a8b78b8027866cbbdc151542a26c` | `QmYxxXdsaPAxbZ2EW98dk33eJTjRu8bzgSeniad39vdWdf` | `QmPbCDtPh4fzibKAHociHnUugDoYAtdSPGK2neJAYPC4Wg` |

## Evidence boundaries

The [candidate ledger](../forks/metadata-recovery/chainlink/replay-results.json) records two authenticated offline compiler replays. Each bundle preserves the input bytes, metadata bytes, source paths, settings, and source Keccak associations. [Compiler pins](RUNTIME_BUILD_INPUTS.md) authenticate the local compiler packages independently of the bundle.

The WETH candidate preserves metadata retrieved from the official npm 0.0.9 artifact. Its bundle origin describes that npm artifact, not the Ethereum deployment. Replay reproduces the npm metadata and runtime exactly. The npm runtime has the same executable code as the WETH proxy, but its metadata CID differs.

The [five-package ledger](../forks/metadata-recovery/chainlink/npm-packages.json) records official registry URLs, publication dates, SHA512 SRI, and observed tarball SHA256 values. The archived [package comparison](../forks/metadata-recovery/chainlink/official-proxy-artifact-results.json) records five candidate failures. Tarballs remain downloadable from those public URLs and are not duplicated here. The selected [npm artifact](../forks/metadata-recovery/chainlink/official-proxy-0.0.9-artifact.json) retains upstream source and license comments.

Seven WETH source contents match the official npm artifact. Twelve USDC source contents match the historical libocr commit `c910dd9a5eae69ec190a110cfe9b31e213dcbba6`. The [source manifest](../forks/metadata-recovery/chainlink/authenticated-source-manifest.json) records their hashes. The USDC bundle contains the unchanged candidate sources with compiler-generated metadata. Source provenance does not establish the deployment's source keys or settings.

The historical [Go generation command](../forks/metadata-recovery/chainlink/libocr-go_generate.go) uses optimizer 15000. Its nearby binding has solc 0.7.5 and no IPFS metadata hash. The deployed aggregator requires solc 0.7.6 and optimizer 20000. That public build does not supply original deployment metadata.

The saved explorer observations explicitly report `SimilarMatch` for [WETH](../forks/metadata-recovery/chainlink/WETH_price_source-explorer-observation.json) and [USDC](../forks/metadata-recovery/chainlink/USDC_price_source_aggregator-explorer-observation.json). These inputs are candidates. The initial deployed-CID gateway requests returned HTTP 429. No additional gateway probes were made during this recovery. This does not establish global unavailability.

Recorded transactions and receipts identify successful direct creations. WETH was created at block `10606501` in transaction `0x7fee27e7df5f16ba3e5bb5fbcb5103f8984e0cacf31cb84f50def5618a7008f2`. USDC aggregator was created at block `12136582` in transaction `0xc62a1a00178513e7727bf3cb26ef68788d1d16501d1d68f110f57c081df32086`. Creation identity does not establish compiler initcode or immutable derivation.

The USDC price proxy already has a separate full-runtime match. The WETH price aggregator belongs to Issue 54. Neither is counted again here.

## Workflow state

| Stage | Result |
| --- | --- |
| 1. Investigation | `BLOCKED`. Both original deployment metadata preimages remain missing. |
| 2. Plan and environment | A dedicated evidence branch retains bounded candidates and restart conditions. This does not unlock target implementation. |
| 3. Implementation | Deferred. No new runtime decoder, constructor binding, or success path is introduced. |
| 4. Verification | Candidate source association and offline replay pass. Deployment CID equality fails for both. Full runtime and aggregator immutable acceptance remain unverified. |
| 5. Review | Independent review reproduced both candidate metadata outputs and CID mismatches, checked all saved hashes and links, and found no actionable issue. Target implementation acceptance is deferred. |
| 6. Delivery | An evidence PR can be reviewed independently. Issue 57 remains open. |

The user's November 14 response identified no original metadata or build-info location. A new API credential is not the missing datum.

## Restart conditions

Either original metadata JSON for both required CIDs or deployment standard JSON input with build-info can reopen Stage 1. The build records must preserve source keys, unchanged contents, full compiler version, optimizer settings, metadata settings, remappings, and libraries. WETH builds for the same-CID BTC/USD `0xf4030086522a5beea4988f8ca5b36dbc97bee88c`, LINK/USD `0x2c1d072e956affc0d435cb7ac38ef18d24d9127c`, or BTC/ETH `0xdeb288f737066589598e9214e782fa5a8ed689e8` proxies may supply the same missing metadata.

A resumed implementation must first reproduce the deployment CID. It must then authenticate the creation transaction, receipt, address, and complete compiler initcode prefix. The aggregator's minAnswer, maxAnswer, decimals, and LINK must come from constructor input and authenticated AST immutable references. Fixed-block getter values are not independent constructor evidence. Signed int192 values, address widths, dynamic description offsets, truncated input, wrong values, and metadata differences require rejection tests before either full runtime can pass.

## Candidate replay commands

With the approved compiler installations described in [Runtime compiler input replay](RUNTIME_BUILD_INPUTS.md), run from the repository root.

```sh
python3 scripts/runtime-build-inputs.py validate forks/metadata-recovery/chainlink/WETH_price_source-candidate-bundle.json
python3 scripts/runtime-build-inputs.py replay forks/metadata-recovery/chainlink/WETH_price_source-candidate-bundle.json --module-root /tmp/aegis-solcjs-0.6.6/node_modules --output /tmp/issue57-weth-output.json
python3 scripts/runtime-build-inputs.py validate forks/metadata-recovery/chainlink/USDC_price_source_aggregator-candidate-bundle.json
python3 scripts/runtime-build-inputs.py replay forks/metadata-recovery/chainlink/USDC_price_source_aggregator-candidate-bundle.json --module-root /tmp/aegis-solcjs-0.7.6/node_modules --output /tmp/issue57-usdc-output.json
```

Successful replay means that the saved candidate compiles under authenticated packages. It does not change either failed deployment CID comparison. The [SHA256 manifest](../forks/metadata-recovery/chainlink/SHA256.json) hashes the saved evidence files as raw bytes.
