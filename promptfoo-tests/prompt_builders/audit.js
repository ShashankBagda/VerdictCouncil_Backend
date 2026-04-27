const { buildChat } = require('./_lib');

module.exports = ({ vars }) =>
  buildChat({
    phase: 'audit',
    vars,
    upstream: [
      { label: 'INTAKE OUTPUT', fixture: 'intake_traffic1' },
      { label: 'RESEARCH OUTPUT (joined)', fixture: 'research_traffic1' },
      { label: 'SYNTHESIS OUTPUT', fixture: 'synthesis_traffic1' },
    ],
  });
