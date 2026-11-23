# Creation-derived runtime evidence

Issue #54 removes the comparison-runtime dependency from thirteen previously supplemented immutable targets. Seven Pool libraries have separate creation proofs. These libraries do not increase the twenty-five logical-target denominator.

The saved [proof](../forks/aave-v3-creation-runtime-19000000.json) records full-byte equality at Ethereum chain 1, block `19000000`, hash `0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac`. Metadata bytes remain in the comparison. This proof does not establish full reproduction of all twenty-five targets or market-wide borrowing protection. The historical Sourcify artifact retains its original results.

## Replay the proof

Install Foundry `v1.8.6`, Node.js, and the three authenticated compiler trees described in [Runtime compiler input replay](RUNTIME_BUILD_INPUTS.md). This proof uses Solidity `0.7.6`, `0.8.10`, and `0.8.19`. Each tree must match every file in `scripts/runtime-compiler-pins.json`. The CI setup installs `0.8.19` first. For each older tree, it selects only that tree's `solc@version` tarball and copies the approved `solc/common/helpers.js` and `solc/common/types.js` from `0.8.19`. These files are part of the older tree's recorded pins. The setup then runs `verify_compiler` for all three trees. Replay itself never supplements compiler files.

Run these commands from the repository root.

```sh
python3 -m unittest discover -s tests -p test_creation_runtime.py -v
python3 scripts/verify-creation-runtime.py --output /tmp/creation-runtime.json
```

The verifier replays the saved standard JSON through the authenticated compiler and checks its complete output hash. It authenticates each saved creation and derives every expected byte before reading the comparison runtime. It makes no HTTP or RPC request by default. The output contains twenty successful records. Any missing, changed, or inconsistent input causes exit code 1 without writing a partial report.

To repeat the fixed-block comparison through the configured read-only RPC, run this command.

```sh
python3 scripts/verify-creation-runtime.py \
  --rpc-env "$HOME/.config/aegis-flow/rpc.env" \
  --output /tmp/creation-runtime-live.json
```

The RPC option repeats chain and anchor preflight and obtains comparison code at the fixed block. Creation authentication still replays the committed public transactions, receipts, headers, and traces. It does not reacquire their RPC responses. The configuration key is `SENTINEL_RPC_URL`. The verifier does not serialize that value.

## Proof boundaries

[`forks/creation-inputs`](../forks/creation-inputs) preserves public Sourcify responses, exact compiler input bundles, transaction and receipt JSON, numbered deployment headers, complete call traces, the evaluation header, and fixed-block code. `SHA256.json` covers every saved input file. `builds.json` binds each target to its input bundle and full compiler output hash. Compiler output hashes use UTF-8 JSON with sorted keys and compact separators. Runtime hashes use raw bytecode bytes. The additional seven libraries reference the validated `pool_impl` build. They have their own creation records and comparison code.

The source response content and licenses remain unchanged. Compiler input capture preserves source contents and compilation settings. The capture translates Sourcify's flattened library map into standard JSON's nested representation. It explicitly requests ABI, metadata, creation bytecode, runtime bytecode, link references, immutable references, and source AST output. Empty original library maps stay empty. The exact resulting bytes are in the authenticated bundle. Their metadata origin is `reconstructed Sourcify v2 metadata`.

`unit-builds` contains compact compiler snapshots for fast mutation tests. Each snapshot retains the relevant contract outputs and immutable declaration nodes. Those snapshots are test inputs only. The verifier always regenerates full compiler output and checks its recorded hash. It never treats a compact snapshot as an authenticated replay.

Transaction, receipt, and header identities must agree. Receipts must succeed. Deployment must precede the evaluation block. The trace root must match the transaction sender, input, and destination. A direct CREATE also binds the receipt address to the sender and nonce. A factory deployment needs exactly one matching successful creation frame with successful ancestors. Sender checks retain the calling execution address through DELEGATECALL. Six Pool libraries are children of a direct outer CREATE. Their own frame, rather than the outer receipt address, identifies the library.

The numbered deployment headers and evaluation header establish canonical consistency in the RPC's view. The verifier does not traverse millions of parent headers or run a consensus light client. Transactions and traces remain RPC observations under this explicit trust model.

## Reviewed immutable recipes

The [family registry](../scripts/runtime-creation-families.json) fixes each target address, compilation contract, complete source-content identity, and immutable declaration path, contract, name, and type. Compiler AST IDs are resolved within each replayed input. IDs are not global constants. A new source identity or declaration requires a reviewed recipe change. Unknown or missing recipes fail.

The reviewed assignments are these source statements.

| Family | Constructor source semantics | Independent value |
| --- | --- | --- |
| Six Aave immutable-admin proxies | `BaseImmutableAdminUpgradeabilityProxy._admin = admin` | Canonical constructor argument 0 |
| Pool implementation | `Pool.ADDRESSES_PROVIDER = provider` | Canonical constructor argument 0 |
| ACL manager | `ACLManager.ADDRESSES_PROVIDER = provider` | Canonical constructor argument 0 |
| Aave oracle | `ADDRESSES_PROVIDER = provider`, `BASE_CURRENCY = baseCurrency`, `BASE_CURRENCY_UNIT = baseCurrencyUnit` | Canonical arguments 0, 4, and 5 |
| Three token implementations | `IncentivizedERC20.POOL = pool`, `_addressesProvider = pool.ADDRESSES_PROVIDER()`, `EIP712Base._chainId = block.chainid` | Argument 0, actual constructor STATICCALL output, and authenticated chain 1 |
| WETH aggregator | `OffchainAggregator.minAnswer = _minAnswer`, `maxAnswer = _maxAnswer`, `decimals = _decimals` | Canonical arguments 6, 7, and 10 |
| Seven Pool libraries | Solidity's library self-address prefix | Authenticated created library address |

For each token implementation, exactly one immediate constructor STATICCALL must invoke selector `0x0542975c` on the constructor Pool argument. Its sender must be the new token implementation. Its successful output must be the Ethereum Provider word. The proof retains the creation frame path and immutable declaration bindings. An ordinary deployment-block `eth_call` cannot replace this constructor context.

Pool's actual creation argument is Provider `0x2f39d218133afab8f2b819b1066c7e434ad94e9e`. The previously recorded API value `0xa97684ead0e402dc232d5a977953df7ecbab3cdb` is rejected. The verifier does not copy immutable words from deployed runtime or creation frame output.

## Maintainer API

[`runtime-creation.py`](../scripts/runtime-creation.py) owns creation identity, compiler-defined linking, canonical constructor decoding, AST binding, and reviewed family derivation. [`verify-creation-runtime.py`](../scripts/verify-creation-runtime.py) owns the comparison and report.

`ValidatedBuild(bundle)` validates and replays an Issue #53 bundle. `authenticate_creation(target, evidence)` authenticates a public creation record. `derive_runtime(validated_build, target, authenticated_creation)` returns expected runtime bytes and provenance. Its signature accepts no comparison runtime. The CLI reads comparison code only after this function returns.

`bind_creation_code(bytecode, initcode)` resolves only compiler-declared twenty-byte creation link spans. Repeated references must agree. Spans must be in bounds and disjoint. Every other creation-prefix byte must match. Runtime links need the corresponding creation binding. `canonical_constructor(abi, suffix)` uses Foundry's JSON ABI decoder, validates its response shape, re-encodes the result, and compares the entire suffix. This rejects invalid widths, signed padding, dynamic offsets, truncation, and trailing data. Contracts with no constructor arguments accept only an empty suffix. These helpers can support later post-link-only families without borrowing runtime bytes. `create2_address(factory, salt, initcode)` computes the CREATE2 formula. It supplies no transaction or factory-policy proof. Later families must authenticate the successful CREATE2 frame and bind its initcode before using this formula.

## Six-stage record

1. Investigation confirmed live Issue #54, its thirteen-target acceptance criteria, Issue #53's authenticated compiler API, and complete public creation evidence. README and SPEC retain their market-integration gates.
2. Planning selected a fixed source-family registry and authenticated creation records. A general EVM interpreter is outside this change. Model the Domain shaped the registry and the build/creation derivation boundary.
3. Red failed because the new module did not exist. Authentication and prefix tests then passed. Mutation tests cover real creation frames, constructor context, ABI, source identity, AST IDs, and overlapping references.
4. Verification compares all thirteen targets and seven additional libraries byte for byte. Offline replay uses authenticated compilers and no network. CI runs real Keccak and ABI tests with pinned Foundry. The exact three-tree CI setup also passed from fresh isolated install prefixes, including the two approved common files for each older compiler. Environments without `cast` skip those tests and cannot claim their success.
5. Independent review and any resulting fixes are recorded in the PR. Prove It Works requires actual compiler replay and fixed-block equality, beyond unit snapshots.
6. Delivery remains a stacked PR against Issue #53. Merge, Issue close, public chain writes, and production acceptance remain separate operations.
