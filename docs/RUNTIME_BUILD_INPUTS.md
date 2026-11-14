# Replay a runtime compiler input offline

`scripts/runtime-build-inputs.py` saves the exact UTF-8 standard JSON input and metadata representation in one JSON bundle. Each blob contains base64 bytes and a SHA256 digest of those raw bytes. Source paths stay JSON keys, including absolute upstream paths. The tool never writes source paths to disk. It preserves settings, including an empty `libraries` object. Link bindings and immutable substitutions belong to separate downstream operations.

Capture an existing input and its metadata representation.

```sh
python3 scripts/runtime-build-inputs.py capture \
  --input compiler-input.json --metadata metadata.json \
  --origin 'compiler-generated metadata' \
  --target configurator_impl --version 0.8.10 --out bundles
```

The command prints the bundle filename and raw-byte SHA256. Use `retrieved original metadata` only for retrieved original bytes. Sourcify v2 parsed objects use `reconstructed Sourcify v2 metadata`. A compiler-generated representation can be compared to a deployment metadata CID by downstream verification. Its presence alone does not establish that CID.

Validate the bundle, then replay it with an existing local compiler installation.

```sh
python3 scripts/runtime-build-inputs.py validate bundles/BUNDLE_SHA256.json
python3 scripts/runtime-build-inputs.py replay bundles/BUNDLE_SHA256.json \
  --module-root /tmp/aegis-solcjs-0.8.10/node_modules --output compiler-output.json
```

Replay makes no fetch requests. The Node driver disables HTTP, HTTPS, socket, TLS, and DNS request methods used by the compiler wrapper. The subprocess receives only `PATH` and `LANG`. It does not inherit RPC credentials, `NODE_OPTIONS`, or `NODE_PATH`.

The metadata compilationTarget must name one source and contract. Replay requires that contract in the compiler output. The metadata compiler identifier must equal the approved complete compiler identifier. The metadata form may omit only `.Emscripten.clang`. Missing sources, malformed hashes, and content changes fail Ethereum Keccak256 association. Additional input sources remain in the bundle and appear under `source_association.unreferenced`. This association authenticates the declared source contents against the supplied metadata representation. It does not independently authenticate the representation against a deployment.

`verify-source-runtime.py` and `verify-sourcify-runtime.py` use the shared compiler and bundle module. Runtime results include `bundle_file`, `bundle_sha256`, the compiler version, the driver digest, executed toolchain file hashes, and the Node version. Source API metadata comes from the compiler output and carries that origin. Sourcify metadata carries the reconstructed origin. Their default bundle directory is `forks/runtime-inputs`. Historical runtime artifacts remain unchanged.

The existing Sourcify `compiled_runtime_sha256` and `deployed_runtime_sha256` fields hash lowercase hex text without `0x`. New fields ending in `_raw_bytes_sha256` hash decoded runtime bytes. Each representation has a separate encoding label.

## Compiler pins

`scripts/runtime-compiler-pins.json` is the independent trust input. Bundles cannot supply or replace compiler file pins. The local driver also has an independent SHA256 pin. Before compiler loading, the Python module compares the local executable and package JSON file set with this manifest. The Node driver checks each resolved dependency against the same file hashes before loading it. Missing files, extra executable files, substituted wrappers, symlinks outside npm `.bin`, and wrong full compiler versions fail. Dependencies cannot resolve outside the authenticated directory.

The recorded versions are 0.4.19, 0.4.24, 0.6.6, 0.6.12, 0.7.6, 0.8.10, and 0.8.19. Pin acquisition fetched each package's exact version from `https://registry.npmjs.org/NAME/VERSION`, authenticated its tarball against the registry SHA512 SRI, and compared the installed files with the authenticated tarball contents. The manifest records the tarball URL and SRI for every package. Across the seven installations, 85 distinct tarballs authenticated 1,154 JavaScript, JSON, native-module, or WebAssembly file entries. Full versions were queried only after those comparisons.

The older local installations contain `solc/common` supplements from official solc 0.8.19. Those exact file hashes are pinned against its authenticated tarball. Runtime verification never copies a fallback. An installation without the approved supplement fails until the user supplies those exact files. Package downloads belong to setup, outside offline replay. Node and operating-system authentication are outside this compiler-package trust boundary. Node's version is recorded as execution context.

To reproduce the CI compiler setup, install the exact package versions listed in the `0.8.19.registry_tarballs` keys with npm scripts disabled. CI verifies the resulting tree before compilation. A changed package tree fails rather than receiving fresh self-generated pins.

## Verification limits

The Foundry-equipped Solidity CI job runs the real Keccak vectors and an authenticated compiler replay. The Rust job can skip optional backend or compiler tests when `cast`, pycryptodome, or the compiler installation is absent. A skipped compiler test does not establish offline replay success. The local Issue 53 verification includes a real replay of the unchanged Issue 56 input with `libraries={}`. Downstream metadata CID, link binding, fixed-block runtime equality, and complete target coverage remain separate checks.
