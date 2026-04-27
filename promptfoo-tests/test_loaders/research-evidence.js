const { loadGolden } = require('./_lib');

module.exports = function () {
  const golden = loadGolden('traffic-1-improper-lane-change');
  return [
    {
      description: `${golden.metadata.id} (single-case demonstration)`,
      vars: { case_input: golden.inputs },
      assert: [
        {
          type: 'javascript',
          value: 'JSON.parse(output).evidence_items.length > 0',
        },
        {
          type: 'javascript',
          value: `
            const items = JSON.parse(output).evidence_items;
            return items.every(it => ['weak','moderate','strong'].includes(it.strength));
          `,
        },
      ],
    },
  ];
};
