const { buildChat } = require('./_lib');

module.exports = ({ vars }) =>
  buildChat({
    phase: 'research-law',
    vars,
    upstream: [{ label: 'INTAKE OUTPUT', fixture: 'intake_traffic1' }],
  });
