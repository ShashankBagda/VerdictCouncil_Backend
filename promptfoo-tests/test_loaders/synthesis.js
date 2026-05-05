const { loadGolden } = require('./_lib');

// Synthesis output follows SynthesisOutput schema field names:
//   arguments.{claimant_arguments, respondent_arguments, contested_points, counter_arguments}
// The schema aligns prompt and Pydantic since v0.4 (agent rename:
//   argument-construction + hearing-analysis → synthesis).
//
// preliminary_conclusion MUST be null — enforced by prompt Hard rules and
// audited by hearing-governance at prompts.py:1658 (CRITICAL_FLAG on non-null).
// Schema keeps str | None so ToolStrategy can soft-catch before the auditor logs.
module.exports = function () {
  const golden = loadGolden('traffic-1-improper-lane-change');
  return [
    {
      description: `${golden.metadata.id} (single-case demonstration)`,
      vars: { case_input: golden.inputs },
      assert: [
        {
          // Both sides present under schema field names (claimant = prosecution, respondent = defence).
          type: 'javascript',
          value: `
            const args = JSON.parse(output).arguments || {};
            return Array.isArray(args.claimant_arguments) && args.claimant_arguments.length >= 1
                && Array.isArray(args.respondent_arguments) && args.respondent_arguments.length >= 1;
          `,
        },
        {
          // Reasoning chain non-empty.
          type: 'javascript',
          value: `
            const chain = JSON.parse(output).reasoning_chain;
            return Array.isArray(chain) && chain.length >= 1;
          `,
        },
        {
          // Prompt mandates preliminary_conclusion is null (three-layer verdict defence).
          type: 'javascript',
          value: 'JSON.parse(output).preliminary_conclusion === null',
        },
        {
          // uncertainty_flags is a declared schema field (replaces legacy pre_hearing_brief).
          type: 'javascript',
          value: `
            const flags = JSON.parse(output).uncertainty_flags;
            return Array.isArray(flags);
          `,
        },
      ],
    },
  ];
};
