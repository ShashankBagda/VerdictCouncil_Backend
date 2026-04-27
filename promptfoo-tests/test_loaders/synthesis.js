const { loadGolden } = require('./_lib');

// Synthesis prompt declares a much richer shape than the schema (drift —
// see research-facts.js). For traffic cases the prompt asks for
// arguments.{prosecution, defence}; the schema declares
// arguments.{claimant_position, respondent_position}. We assert what the
// prompt actually emits.
//
// The prompt also explicitly states:
//     `preliminary_conclusion` and `confidence_score` MUST be `null`.
// The schema declares preliminary_conclusion: str (min_length=1) — direct
// contradiction between prompt and schema. We assert per the prompt.
module.exports = function () {
  const golden = loadGolden('traffic-1-improper-lane-change');
  return [
    {
      description: `${golden.metadata.id} (single-case demonstration)`,
      vars: { case_input: golden.inputs },
      assert: [
        {
          // Traffic-domain arguments shape.
          type: 'javascript',
          value: `
            const args = JSON.parse(output).arguments || {};
            return typeof args.prosecution === 'object' && args.prosecution !== null
                && typeof args.defence === 'object' && args.defence !== null;
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
          // Prompt mandates preliminary_conclusion is null.
          type: 'javascript',
          value: 'JSON.parse(output).preliminary_conclusion === null',
        },
        {
          // Pre-hearing brief — present and non-empty (string or object).
          type: 'javascript',
          value: `
            const pb = JSON.parse(output).pre_hearing_brief;
            return pb !== undefined && pb !== null
                && (typeof pb === 'string' ? pb.length > 0 : Object.keys(pb).length > 0);
          `,
        },
      ],
    },
  ];
};
