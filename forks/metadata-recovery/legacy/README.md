# Legacy metadata recovery evidence

This directory records negative evidence for [Issue #55](https://github.com/takurot/aegis-flow/issues/55), acquired on 2026-11-14. Original deployment metadata remains unavailable. [The six-stage record](../../../docs/ISSUE_55.md) keeps Stage 1 blocked.

## Runtime and reference observations

The saved read-only RPC snapshot follows a chain and pinned-block preflight. It contains no RPC endpoint. The target is Ethereum chain `1`, block `19000000`, hash `0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac`.

| Target | Address | Runtime bytes | Execution-code bytes | Observed reference | Deployed digest |
| --- | --- | --- | --- | --- | --- |
| WETH | `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2` | 3124 | 3081 | `bzzr0` | `deb4c2ccab3c2fdca32ab3f46728389c2fe2c165d5fafa07661e4e004f6c344a` |
| USDC proxy | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` | 2186 | 2143 | `bzzr0` | `a4a547cfc7202c5acaaae74d428e988bc62ad5024eb0165532d3a8f91db4ed24` |

Both tails have the 43-byte form `a165627a7a72305820<32-byte digest>0029` described by the [Solidity 0.4.19 metadata specification](https://docs.soliditylang.org/en/v0.4.19/metadata.html) and [0.4.24 specification](https://docs.soliditylang.org/en/v0.4.24/metadata.html). These observed tails contain a Swarm reference without a compiler-version field. They are not empty IPFS references or evidence of absent metadata. This table is an observation, not a product decoder implementation. Typed handling of other reference kinds remains unmet.

`legacy-retrieval-results.json` retains legacy `bzzr:/` and candidate Bee HTTP responses. `raw-retrieval-results.json` retains the distinct `bzz-raw:/` attempts and local DNS-label errors. HTTP 404 is specific to each observed request. Bee compatibility with the legacy hash remains unproved. A local `UnicodeError` for a 64-character DNS label provides no network-availability evidence.

## Candidate sources and provenance

`WETH-bundle.json` contains exact raw Standard JSON input and compiler-generated metadata encoded as base64. Its source was retrieved through the [Sourcify contract API](https://sourcify.dev/server/v2/contract/1/0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2?fields=metadata,sources,compilation). The source content equals the [earliest canonical WETH source](https://github.com/gnosis/canonical-weth/blob/3c3b0292fb007d41d5d718bb3195f824a0222b80/contracts/WETH9.sol) after CRLF normalization. `canonical-weth-2018.sol` retains the retrieved historical bytes. The [first committed canonical artifact](https://github.com/gnosis/canonical-weth/blob/a8c5ab71f792552b11f961ac29fcbc0c16fa8be1/build/contracts/WETH9.json) reports 0.4.21 and has the wrong suffix. Neither source agreement nor that later artifact proves original deployment inputs.

`USDC-bundle.json` contains the five-source graph rooted at `contracts/FiatTokenProxy.sol` from [Circle commit fab8640](https://github.com/circlefin/stablecoin-evm/blob/fab8640fd3bc4ad8ee111a001a3ee9ee877ccc56/contracts/FiatTokenProxy.sol). The retained `circle-2018-package.json` pins [zos-lib 1.3.0](https://registry.npmjs.org/zos-lib/-/zos-lib-1.3.0.tgz) and [openzeppelin-solidity 1.11.0](https://registry.npmjs.org/openzeppelin-solidity/-/openzeppelin-solidity-1.11.0.tgz). The bundle preserves three upgradeability imports from zos-lib and `AddressUtils.sol` from OpenZeppelin. Each source has a SHA-256 in `replay-results.json` and a metadata-declared Keccak checked by Issue #53 capture and validation. This association does not prove the original deployed Swarm hash.

The raw [Circle comparison instructions](https://github.com/circlefin/stablecoin-evm/blob/fab8640fd3bc4ad8ee111a001a3ee9ee877ccc56/validate/README.contractDiff.md) describe flattened explorer-source differences, including imports, whitespace, and comments. `USDC-flattened-input.json` retains the separate [Sourcify proxy input](https://sourcify.dev/server/v2/contract/1/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48?fields=metadata,sources,compilation) used in the earlier path experiments.

The raw [Docker layout](https://github.com/circlefin/stablecoin-evm/blob/fab8640fd3bc4ad8ee111a001a3ee9ee877ccc56/docker-compose.yml) supplies the `/usr/local/solidity` path hypothesis. The raw [build specification](https://github.com/circlefin/stablecoin-evm/blob/fab8640fd3bc4ad8ee111a001a3ee9ee877ccc56/buildspec.yaml) archives `build/contracts/*`. The [CI template](https://github.com/circlefin/stablecoin-evm/blob/fab8640fd3bc4ad8ee111a001a3ee9ee877ccc56/ci/codebuild.yml) specifies private S3 artifacts with `BUILD_ID` namespaces. No private artifact was requested or retrieved. `historical-build-records.json` retains exact base64 bytes, SHA-256 values, and source URLs for the comparison instructions, Docker layout, and build specification. Encoding preserves upstream whitespace without introducing whitespace errors in the evidence diff.

## Bounded candidate ledger

`candidate-ledger.json` preserves results, exact source hashes, source keys, and settings for every recorded attempt. The group names identify the acquisition result files.

| Group | Compiled comparisons | Variants | Result |
| --- | --- | --- | --- |
| `candidate-results.json` | 18 | WETH 0.4.19 at five source paths and USDC 0.4.24 at four paths, each with retrieved and LF-normalized source | Execution code equal. Full runtime unequal. |
| `weth-compiler-results.json` | 10 | Canonical WETH 0.4.18 at five paths, each LF and CRLF | Execution code equal. Full runtime unequal. Compiler not approved by current policy. |
| `multifile-results.json` | 8 | Circle five-source graph at empty, `./`, `/project/`, and `/app/` roots, each LF and CRLF | Execution code equal. Full runtime unequal. |
| `multifile-docker-results.json` | 2 | Circle graph at `/usr/local/solidity/`, each LF and CRLF | Execution code equal. Full runtime unequal. |

Five additional path attempts used [Dapphub commit 88d8f7e](https://github.com/dapphub/ds-weth/blob/88d8f7eac7ad5c2b1318b57895814b097e870be0/src/weth9.sol). That source declares `WETH9_`, so no `WETH9` target was produced. Those attempts are excluded from the 38 comparisons. `/project/` and `/app/` were hypotheses. These attempts do not exhaust historical Truffle path resolution, remappings, build settings, or native compilers.

`acquisition-artifact-hashes.json` preserves the original acquisition manifest, including hashes for artifacts outside this compact directory. It is historical inventory, not a claim that every listed file is committed here. `SHA256SUMS` covers the files actually retained here. Bundles preserve raw input and metadata bytes without JSON reserialization of those embedded inputs.

`replay-results.json` records two repeat compilations using current approved toolchain pins. The compiler-generated metadata matches the saved candidate metadata byte for byte. The complete runtimes still differ from the deployed snapshot. Execution-code comparison excludes only the observed 43-byte legacy tail for diagnosis. Full comparison includes every byte and remains false. No runtime metadata was copied or patched.

## Repeat the primary candidate checks

Use Node, Python, `cast`, and the pinned compiler modules installed at `/tmp/aegis-solcjs-0.4.19/node_modules` and `/tmp/aegis-solcjs-0.4.24/node_modules`. Toolchain manifests are checked by the existing replay command. From the repository root, run these commands. The expected CLI output is `validated` and `replayed`, not a runtime-equality assertion.

```sh
python3 scripts/runtime-build-inputs.py validate forks/metadata-recovery/legacy/WETH-bundle.json
python3 scripts/runtime-build-inputs.py replay forks/metadata-recovery/legacy/WETH-bundle.json --output /tmp/issue55-WETH-output.json
python3 scripts/runtime-build-inputs.py validate forks/metadata-recovery/legacy/USDC-bundle.json
python3 scripts/runtime-build-inputs.py replay forks/metadata-recovery/legacy/USDC-bundle.json --output /tmp/issue55-USDC-output.json
(cd forks/metadata-recovery/legacy && shasum -a 256 -c SHA256SUMS)
```

The following comparison consumes those actual outputs and the saved snapshot. It asserts the expected failure of full equality and the exact saved metadata bytes.

```python
import base64
import json
from pathlib import Path

root = Path('forks/metadata-recovery/legacy')
actual = json.loads((root / 'actual-runtime.json').read_bytes())
for name in ('WETH', 'USDC'):
    bundle = json.loads((root / (name + '-bundle.json')).read_bytes())
    output = json.loads(Path('/tmp/issue55-' + name + '-output.json').read_bytes())
    target = bundle['compilation_target']
    compiled = output['contracts'][target['source_path']][target['contract_name']]
    runtime = compiled['evm']['deployedBytecode']['object']
    assert compiled['metadata'].encode() == base64.b64decode(bundle['metadata']['base64'])
    assert len(runtime) == len(actual[name])
    assert runtime[:-86] == actual[name][:-86]
    assert runtime != actual[name]
    print(name, len(runtime) // 2, 'code equal; full runtime unequal')
```
