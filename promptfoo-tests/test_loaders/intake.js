const fs = require('node:fs');
const path = require('node:path');

const GOLDEN_CASES_DIR = path.resolve(
  __dirname,
  '..',
  '..',
  'tests',
  'eval',
  'data',
  'golden_cases',
);

function generate_tests() {
  const files = fs
    .readdirSync(GOLDEN_CASES_DIR)
    .filter((name) => name.endsWith('.json'))
    .sort();

  return files.map((filename) => {
    const fullPath = path.join(GOLDEN_CASES_DIR, filename);
    const golden = JSON.parse(fs.readFileSync(fullPath, 'utf8'));
    const expected = golden.expected?.intake ?? {};

    const assertions = [
      {
        type: 'javascript',
        value: `JSON.parse(output).domain === ${JSON.stringify(expected.domain)}`,
      },
      {
        type: 'javascript',
        value: `JSON.parse(output).parties.length === ${expected.parties_count}`,
      },
    ];

    if (expected.offence_code !== undefined) {
      assertions.push({
        type: 'javascript',
        value: `JSON.parse(output).case_metadata.offence_code === ${JSON.stringify(expected.offence_code)}`,
      });
    }

    return {
      description: golden.metadata?.id ?? filename.replace(/\.json$/, ''),
      vars: { case_input: golden.inputs },
      assert: assertions,
    };
  });
}

module.exports = generate_tests;
