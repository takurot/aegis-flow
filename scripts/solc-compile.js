const solc = require(process.argv[2]);
const compile = solc.compileStandard || solc.compileStandardWrapper || solc.compile;
let input = '';
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  try {
    const output = compile(input);
    process.stdout.write(typeof output === 'string' ? output : JSON.stringify(output));
  } catch (error) {
    process.stderr.write(String(error));
    process.exit(1);
  }
});
