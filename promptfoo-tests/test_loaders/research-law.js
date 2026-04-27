const { loadGolden } = require('./_lib');

// Targets prompt-declared LegalRule fields (statute_name, section, tier),
// not the schema's older citation/text/applicability. See research-facts.js
// for the drift rationale.
module.exports = function () {
  const golden = loadGolden('traffic-1-improper-lane-change');
  return [
    {
      description: `${golden.metadata.id} (single-case demonstration)`,
      vars: { case_input: golden.inputs },
      assert: [
        {
          type: 'javascript',
          value: 'JSON.parse(output).legal_rules.length > 0',
        },
        {
          // RTA s.65 should appear in at least one rule's statute_name + section.
          type: 'javascript',
          value: `
            const rules = JSON.parse(output).legal_rules;
            return rules.some(r =>
              /road\\s*traffic\\s*act|rta/i.test(r.statute_name || '') &&
              /65/.test(r.section || '')
            );
          `,
        },
        {
          // Tier should be in the 1-6 authority hierarchy from the prompt.
          type: 'javascript',
          value: `
            const rules = JSON.parse(output).legal_rules;
            return rules.every(r => Number.isInteger(r.tier) && r.tier >= 1 && r.tier <= 6);
          `,
        },
        {
          type: 'javascript',
          value: 'Array.isArray(JSON.parse(output).precedents)',
        },
      ],
    },
  ];
};
