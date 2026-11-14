const fs = require('fs');
const path = require('path');
const Module = require('module');
const crypto = require('crypto');
const pins = require('./runtime-compiler-pins.json')[process.argv[3]].files_sha256;
for (const name of ['http', 'https', 'net', 'tls', 'dns']) {
  const transport = require(name);
  for (const method of ['request', 'get', 'connect', 'createConnection', 'lookup', 'resolve']) {
    if (typeof transport[method] === 'function') {
      transport[method] = () => { throw new Error('Network disabled for compiler replay'); };
    }
  }
}
const root = fs.realpathSync(path.dirname(process.argv[2]));
const resolve = Module._resolveFilename;
Module._resolveFilename = function (...args) {
  const resolved = resolve.apply(this, args);
  if (path.isAbsolute(resolved) && !resolved.startsWith(root + path.sep)) {
    throw new Error('Compiler dependency outside authenticated tree');
  }
  if (path.isAbsolute(resolved)) {
    const relative = path.relative(root, resolved).split(path.sep).join('/');
    const digest = crypto.createHash('sha256').update(fs.readFileSync(resolved)).digest('hex');
    if (pins[relative] !== digest) {
      throw new Error('Compiler input does not match independent pin');
    }
  }
  return resolved;
};
const solc = require(process.argv[2]);
if (process.argv[4] === '--version') {
  process.stdout.write(solc.version());
} else {
  const compile = solc.compileStandard || solc.compileStandardWrapper || solc.compile;
  let input = '';
  process.stdin.on('data', (chunk) => { input += chunk; });
  process.stdin.on('end', () => {
    try {
      const output = compile(input);
      process.stdout.write(typeof output === 'string' ? output : JSON.stringify(output));
    } catch (error) {
      process.stderr.write('Compiler execution failed');
      process.exit(1);
    }
  });
}
