const fs = require('node:fs');
const path = require('node:path');

const PROMPTS_DIR = path.resolve(__dirname, '..', '..', 'prompts');
const FIXTURES_DIR = path.resolve(__dirname, '..', 'fixtures');

function readPrompt(phase) {
  return fs.readFileSync(path.join(PROMPTS_DIR, `${phase}.md`), 'utf8');
}

function readFixture(name) {
  const raw = JSON.parse(
    fs.readFileSync(path.join(FIXTURES_DIR, `${name}.json`), 'utf8'),
  );
  delete raw._comment;
  return raw;
}

function buildChat({ phase, vars, upstream = [] }) {
  const sections = [
    'CASE INPUT:\n```json\n' +
      JSON.stringify(vars.case_input, null, 2) +
      '\n```',
  ];
  for (const { label, fixture } of upstream) {
    sections.push(
      `${label}:\n\`\`\`json\n${JSON.stringify(readFixture(fixture), null, 2)}\n\`\`\``,
    );
  }
  return [
    { role: 'system', content: readPrompt(phase) },
    {
      role: 'user',
      content:
        sections.join('\n\n') +
        `\n\nUsing the above context, emit the structured JSON object the ${phase} phase prompt requires.`,
    },
  ];
}

module.exports = { buildChat, readPrompt, readFixture };
