const fs = require('node:fs');
const path = require('node:path');

const SYSTEM_PROMPT_PATH = path.resolve(
  __dirname,
  '..',
  '..',
  'prompts',
  'intake.md',
);

function build({ vars }) {
  const systemPrompt = fs.readFileSync(SYSTEM_PROMPT_PATH, 'utf8');
  return [
    { role: 'system', content: systemPrompt },
    {
      role: 'user',
      content:
        'Process this case and emit a single IntakeOutput JSON object.\n\n' +
        'CASE INPUT:\n```json\n' +
        JSON.stringify(vars.case_input, null, 2) +
        '\n```',
    },
  ];
}

module.exports = build;
