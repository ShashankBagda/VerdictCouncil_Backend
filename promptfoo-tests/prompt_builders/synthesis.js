const { buildChat } = require('./_lib');

module.exports = ({ vars }) =>
  buildChat({
    phase: 'synthesis',
    vars,
    upstream: [
      { label: 'INTAKE OUTPUT', fixture: 'intake_traffic1' },
      { label: 'RESEARCH OUTPUT (joined)', fixture: 'research_traffic1' },
    ],
  });
