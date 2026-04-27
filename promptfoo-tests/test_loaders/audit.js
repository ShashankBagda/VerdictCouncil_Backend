const { loadGolden } = require('./_lib');

// promptfoo's javascript assertion auto-wraps the value in `function(output)`
// when it sees `return`. To use a `return` we keep the body inline; bare
// expressions must NOT use `return` (they're wrapped as expressions).
module.exports = function () {
  const golden = loadGolden('traffic-1-improper-lane-change');
  const validStatuses = [
    'draft','extracting','awaiting_intake_confirmation','pending','processing',
    'ready_for_review','escalated','closed','failed','failed_retryable',
    'awaiting_review_gate1','awaiting_review_gate2','awaiting_review_gate3','awaiting_review_gate4',
  ];
  const validRerunPhases = ['intake','research','synthesis'];

  return [
    {
      description: `${golden.metadata.id} (single-case demonstration)`,
      vars: { case_input: golden.inputs },
      assert: [
        {
          type: 'javascript',
          value: `
            const fc = JSON.parse(output).fairness_check;
            return typeof fc.audit_passed === 'boolean'
              && typeof fc.critical_issues_found === 'boolean';
          `,
        },
        {
          type: 'javascript',
          value: `${JSON.stringify(validStatuses)}.includes(JSON.parse(output).status)`,
        },
        {
          type: 'javascript',
          value: 'typeof JSON.parse(output).should_rerun === "boolean"',
        },
        {
          type: 'javascript',
          value: `
            const tp = JSON.parse(output).target_phase;
            return tp === null || tp === undefined || ${JSON.stringify(validRerunPhases)}.includes(tp);
          `,
        },
      ],
    },
  ];
};
