# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2026 Vertel Sverige AB (<https://vertel.se>).
#    All Rights Reserved
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
"""Arv av ai.coworker: powerbox-transcript-hook (D2).

Core:s `powerbox()` skapar sessionen utan `interface_key`/`text_selection`
(den känner inte till powerbox-ytan). Denna brygga fångar dem — från
`env.context`-nycklarna `_ai_interface_key`/`_ai_text_selection` (satta av
frontend/controllern) eller från anropets kwargs — och skriver dem på den
nyskapade sessionen så att `transcript_context` kan byggas.

Brygg-mönstret: kärnan (`ai_agent_core`) nämner aldrig denna modul; hooken
ligger i tillägget och är no-op när nycklarna saknas.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class AICoworkerTranscript(models.Model):
    _inherit = 'ai.coworker'

    def powerbox(self, prompt, res_model=None, res_id=None, record=None,
                 interface_key=None, text_selection=None, frontend_info=None):
        """Powerbox med transcript-kontext.

        Fångar interface_key/text_selection/frontend_info och skriver dem på
        den session som `powerbox()` skapar. Värdena tas från explicita
        kwargs, annars från `env.context` (frontend sätter dem där).
        """
        ctx = self.env.context
        interface_key = interface_key or ctx.get('_ai_interface_key')
        text_selection = text_selection or ctx.get('_ai_text_selection')
        frontend_info = frontend_info or ctx.get('_ai_frontend_info')

        # Skapa sessionen med transcript-fälten satta. Vi gör det genom att
        # lägga värdena i context och låta en `create`-hook fånga dem — enklare
        # och robustare än att gräva i core-implementationen.
        self = self.with_context(
            _ai_interface_key=interface_key,
            _ai_text_selection=text_selection,
            _ai_frontend_info=frontend_info,
        )
        return super().powerbox(
            prompt, res_model=res_model, res_id=res_id, record=record)
