const { loadGolden } = require('./_lib');

// Asserts target the fields the prompt declares (research-facts.md "Output
// contract"), not the leaner ExtractedFactItem Pydantic schema. The prompt
// and schema are in drift; production reconciles via ToolStrategy(schema)
// at the agent factory, but this eval drives the model directly so we
// validate the prompt's own contract.
const VALID_CONFIDENCE_LEVELS = [
  'verified', 'corroborated', 'single_source', 'disputed',
  'uncorroborated', 'contradicted',
];
const VALID_MATERIALITY = ['critical', 'important', 'peripheral'];

module.exports = function () {
  const golden = loadGolden('traffic-1-improper-lane-change');
  return [
    {
      description: `${golden.metadata.id} (single-case demonstration)`,
      vars: { case_input: golden.inputs },
      assert: [
        {
          type: 'javascript',
          value: 'JSON.parse(output).facts.length > 0',
        },
        {
          type: 'javascript',
          value: `
            const facts = JSON.parse(output).facts;
            const valid = ${JSON.stringify(VALID_CONFIDENCE_LEVELS)};
            return facts.every(f => valid.includes(f.confidence_level));
          `,
        },
        {
          type: 'javascript',
          value: `
            const facts = JSON.parse(output).facts;
            const valid = ${JSON.stringify(VALID_MATERIALITY)};
            return facts.every(f => valid.includes(f.materiality));
          `,
        },
        {
          type: 'javascript',
          value: 'Array.isArray(JSON.parse(output).timeline)',
        },
      ],
    },
  ];
};
