import logging
from odoo.tests import tagged
from ._config_param_guard import ConfigParamGuardedCase


@tagged('okf', 'retention', 'post_install', '-at_install')
class TestProbeMk(ConfigParamGuardedCase):
    def test_probe(self):
        logger = logging.getLogger(
            'odoo.addons.ai_agent_core.models.ai_okf_concept')
        with self.assertLogs(logger, level='INFO') as logs:
            print('PROBE A disabled=%r eff=%r' % (
                logger.disabled, logger.getEffectiveLevel()))
            print('PROBE B isEnabled=%r' % logger.isEnabledFor(logging.INFO))
            r = logger.makeRecord(logger.name, logging.INFO, 'f', 1,
                                  'probe', None, None)
            print('PROBE C rec-level=%r rec-name=%r' % (r.levelno, r.name))
            print('PROBE D isEnabled-rec=%r' % logger.isEnabledFor(r.levelno))
            logger.handle(r)
            print('PROBE E records=%r' % (logs.records,))
