# USDC V2.2 runtime reproduction

Issue [#58](https://github.com/takurot/aegis-flow/issues/58) requires the complete
USDC V2.2 implementation runtime at Ethereum block `19000000`. The
[recorded result](../forks/aave-v3-usdc-runtime-19000000.json) reproduces all
23,464 bytes, including the original metadata CID. The target is
`USDC_impl`, `0x43506849d7c04f9138d1a2050bbf3a0c054402dd`, on chain 1 at block
hash `0xcf384012b91b081230cdf17a3f7dd370d8e67056058af6b272b3d54aa2714fac`.
This single-target proof does not close the 25-target acceptance gate, Issue
#8's enforcement gate, or production readiness. The legacy USDC proxy remains
Issue #55's separate target.

## Replay

Prepare the approved compiler packages described in
[Runtime build inputs](RUNTIME_BUILD_INPUTS.md). Then run:

```sh
python3 scripts/verify-usdc-runtime.py --output /tmp/usdc-runtime.json
python3 -m unittest discover -s tests -p test_usdc_runtime.py -v
```

The CLI authenticates the compiler tree, replays the saved source-associated
bundle, derives library links from creation input, and compares every byte
against the saved fixed-block runtime. A successful offline run makes no HTTP
or RPC calls. Tests prohibit those calls. The CLI exits 0 on success and exits
1 with a fixed diagnostic on incomplete or inconsistent evidence. A failed run
does not create a new partial report.

A separate read-only live comparison uses the existing secret-safe configuration
parser and chain/block-hash preflight:

```sh
python3 scripts/verify-usdc-runtime.py --rpc-env /path/to/rpc.env --output /tmp/usdc-live.json
```

The configured endpoint is never included in the report or retained evidence.

## Source and original metadata

The [input directory](../forks/usdc-inputs/) retains the bundle, original source
API response, historical source comparisons, exact historical configuration
bytes, creation evidence, and a raw-byte SHA256 manifest. Source comments,
licenses, paths, optimizer settings, and source content remain intact.

The build uses solc `0.6.12+commit.27d51765.Emscripten.clang`, optimizer
10,000,000 and Istanbul. All 19 Circle source contents match official commit
[`a1b5455f1779d8840b6b53cc562f5cc3d0a4875d`](https://github.com/circlefin/stablecoin-evm/tree/a1b5455f1779d8840b6b53cc562f5cc3d0a4875d).
Four OpenZeppelin sources match contracts 3.4.2. The package SHA512 SRI matches that commit's retained `yarn.lock`. The
raw tarball SHA256 is recorded separately. Exact raw bytes of
`package.json`, `truffle-config.js`, and `yarn.lock` are base64-encoded in
`historical-build-records.json`, with hashes and the public URLs in
`circle-historical-config-fetches.json`. Scratch filenames in acquisition
records describe their historical locations; replay uses the retained bytes.
The verifier checks all 23 source hashes against the validated build input.

Public source equivalence alone does not authenticate deployment paths.
Compiling these paths with the original empty `settings.libraries={}`
reproduces deployed metadata CID
`QmNhkGfa5zEkiTbpnDmyvm2DjSGTFDazKhc5xbZq3Sgs58`. The bundle labels its metadata
origin as compiler-generated. It does not claim that original metadata was
retrieved from an IPFS gateway. `outputSelection` was expanded only to obtain
ABI and creation bytecode for validation. This setting is not part of metadata;
the emitted metadata remains identical to the captured metadata.

Adding SignatureChecker to compile-time library settings reproduces a different
metadata CID. Tests actually compile that candidate and reject it despite equal
execution code. Another test compiles changed source paths and rejects the
changed CID. Metadata stripping is used only to explain a negative candidate;
acceptance always compares the complete runtime.

## Creation and postcompile library binding

The successful direct-CREATE transaction
`0x8240d79c3cbdc9347c602def2a433e006ddd5cbd065963a8f67050bb8d8102e2`
was included at block `18921498`. The verifier authenticates its transaction,
receipt, block header, callTracer root, sender/nonce-derived creation address,
transaction index, and fixed-block anchor. The expected value never consumes
the trace's returned runtime or comparison runtime. Canonicality is limited to
RPC canonical-header consistency; full parent-header ancestry is not traversed.

The compiler's two creation link references, byte offsets `18494` and `20209`,
both independently yield SignatureChecker
`0x800c32eaa2a6c93cf4cb51794450ed77fbfbb172`. Every other compiler initcode byte
must match. The constructor suffix is empty, and the runtime has no immutable
references. The shared `derive_linked_runtime` helper from Issue #56 changes
only compiler-declared runtime link ranges. The existing 20-target registry
and its derivation rules remain unchanged.

SignatureChecker's separately retrieved source matches the implementation
build's library source and its metadata-declared Keccak. Its nonempty
fixed-block code has Keccak
`0x76cfc2ce63b1f05cb58ae553090665c2aabf2e13cf8a0e6da755c096fc5295dd`.
This establishes the linked address's source/code provenance, not a complete
transitive dependency audit.

The resulting runtime's raw-byte SHA256 is
`21a831a7cff1a353b295490f43a66e48e55d1c76c60cd89b24138efd8e8b7c8a`.
Compiler-output hashing uses UTF-8 JSON with sorted keys and compact separators;
the report explicitly labels this different encoding.

## Six-stage record

1. Investigation checked live Issue #58 and comments, WORKFLOW, SPEC, README,
   current code, and verified prerequisites. The dedicated branch starts at
   Issue #56 commit `8eadb02804cbe12eb46cd43c632de4c0522092c8`, whose independent
   review and exact-head CI passed. Original metadata recovery had already
   succeeded before target implementation began.
2. The plan retained a fixed single-target data shape and reused the reviewed
   empty-constructor/no-immutable helper. Model the Domain kept this consumer
   out of the existing fixed 20-target family registry.
3. Red reproduced a missing CLI import. Green derived the original metadata and
   complete runtime from authenticated input and creation evidence. No shared
   helper changes were required.
4. Tests reject coordinated transaction/trace prefix, library, truncation and
   suffix edits, metadata-only and execution-code changes, wrong source path,
   source hashes, historical source hashes, receipt, nonce, library source/code,
   target, block, evidence hashes/scope, and incomplete/malformed CLI inputs.
   Existing flat library-name normalization remains covered by the full suite.
   The clean solc 0.6.12 installation authenticates independently pinned files.
   Prove It Works requires actual compiler replay and full-byte offline/live
   comparison, plus regression replay of Issue #54 and #56 proofs.
5. Independent review passed with no P1/P2 findings. The reviewer ran all
   143 Python tests and seven focused tests, authenticated a fresh compiler
   installation with 105 file pins, reproduced the saved report with network
   calls blocked, and replayed the existing Issue #54 and #56 proofs. A fresh
   comment audit found no actionable flags. The parent independently compared
   all live runtime bytes and freshly retrieved all 23 official source contents
   and the package tarball to verify source hashes and SRI.
6. The PR and exact-head terminal CI are verified during delivery. No merge,
   Issue close, token issuance, upgrade, or public-chain write is performed.
