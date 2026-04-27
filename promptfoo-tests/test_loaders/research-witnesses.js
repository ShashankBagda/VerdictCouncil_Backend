const { loadGolden } = require('./_lib');

// Asserts target prompt-declared Witness fields (category, party_alignment,
// formal_statement_exists, motive_to_fabricate), not the schema's
// role/statements/credibility. See research-facts.js for the drift rationale.
module.exports = function () {
  const golden = loadGolden('traffic-1-improper-lane-change');
  return [
    {
      description: `${golden.metadata.id} (single-case demonstration)`,
      vars: { case_input: golden.inputs },
      // traffic-1 carries party + officer testimony in its source documents
      // — a healthy run should identify them as witnesses.
      assert: [
        {
          type: 'javascript',
          value: 'Array.isArray(JSON.parse(output).witnesses)',
        },
        {
          // Each witness should declare a category and a party alignment.
          type: 'javascript',
          value: `
            const ws = JSON.parse(output).witnesses;
            return ws.every(w =>
              typeof w.category === 'string' && w.category.length > 0 &&
              ['claimant','respondent','prosecution','defence','neutral'].includes(
                String(w.party_alignment || '').toLowerCase()
              )
            );
          `,
        },
        {
          // formal_statement_exists is a boolean flag the prompt requires.
          type: 'javascript',
          value: `
            const ws = JSON.parse(output).witnesses;
            return ws.every(w => typeof w.formal_statement_exists === 'boolean');
          `,
        },
      ],
    },
  ];
};
