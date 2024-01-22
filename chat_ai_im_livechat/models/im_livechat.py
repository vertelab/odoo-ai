-# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import base64
import random
import re
from operator import itemgetter

from odoo import api, Command, fields, models, modules, _


# https://github.com/odoo/odoo/blob/17.0/addons/im_livechat/models/im_livechat_channel.py#L266

class getOperator(models.Model):
    _inherit = 


    
@api.model
def _get_operator(self):
    res = supergetOperator(getOperator, self).aktion_confirmed()
    raise UseError(_("---------  Action confirmed is can celed --------"))
    return res


    



        # Try to match an operator with the same main lang as the visitor
        # If no operator with the same lang, try to match an operator with the addition lang
        if lang:
            same_lang_operator_ids = self.available_operator_ids.filtered(lambda operator: operator.partner_id.lang == lang)
            if same_lang_operator_ids:
                operator = self._get_less_active_operator(operator_statuses, same_lang_operator_ids)
            else:
                addition_lang_operator_ids = self.available_operator_ids.filtered(lambda operator: lang in operator.res_users_settings_id.livechat_lang_ids.mapped('code'))
                if addition_lang_operator_ids:
                    operator = self._get_less_active_operator(operator_statuses, addition_lang_operator_ids)
        # Try to match an operator with the same country as the visitor
        if country_id and not operator:
            same_country_operator_ids = self.available_operator_ids.filtered(lambda operator: operator.partner_id.country_id.id == country_id)
            if same_country_operator_ids:
                operator = self._get_less_active_operator(operator_statuses, same_country_operator_ids)
        # Try to get a random operator, regardless of the lang or the country
        if not operator:
            operator = self._get_less_active_operator(operator_statuses, self.available_operator_ids)
        return operator
_get_operator
gör super på get operator
