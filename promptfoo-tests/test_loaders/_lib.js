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

function loadGolden(id) {
  const filename = `${id}.json`;
  return JSON.parse(
    fs.readFileSync(path.join(GOLDEN_CASES_DIR, filename), 'utf8'),
  );
}

module.exports = { loadGolden };
