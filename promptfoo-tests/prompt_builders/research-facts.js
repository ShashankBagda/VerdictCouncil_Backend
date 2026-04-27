const { buildChat } = require('./_lib');

module.exports = ({ vars }) =>
  buildChat({
    phase: 'research-facts',
    vars,
    upstream: [{ label: 'INTAKE OUTPUT', fixture: 'intake_traffic1' }],
  });
