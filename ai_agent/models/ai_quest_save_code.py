from lxml import etree
from odoo import models, api
from odoo.exceptions import UserError
import base64
import io
import zipfile


class AiQuest(models.Model):
    _inherit = 'ai.quest'
    
    def external_id(self,module,object):
        xml_id = object.get_external_id().get(object.id)
        if not xml_id:
            xml_id = f"{module}.{object._name.replace('.', '_')}_{object.id}"
 
    def to_xml(self,record):
        """Return XML-data for record."""
        record_elem = etree.Element('record', model=record._name, id=record._name.replace('.', '_')+"_"+str(record.id))        
        for field in record._fields:
            value = getattr(record, field)
            if value and not record._fields[field].type in ('one2many', 'many2many'):
                field_elem = etree.SubElement(record_elem, 'field', name=field)
                field_elem.text = str(value)
        return etree.tostring(record_elem, pretty_print=True, encoding='unicode')
    
    @api.model
    def generate_simple_module(self, module_name, description="Simple example module", author="Your Name"):
        record_ids = []
        xml_body = "\n<!-- Full records: Quest, agent memory and tools -->"

        ai_quest_id = f"{module_name}.{self._name.replace('.', '_')}"

        tools = self.env['ai.tool']
        memory = self.env['ai.memory']
        agent = self.env['ai.agent']
        for a in self.ai_agent_ids.mapped('ai_agent_id'):
            agent |= a
            memory |= a.ai_memory_ids.mapped('ai_memory_id')
            tools |= a.ai_tool_ids.mapped('ai_tool_id')
        # Create full record
        xml_body += self.to_xml(self)
        for o in agent:
            xml_body += self.to_xml(o)        
        for o in memory:
            xml_body += self.to_xml(o)       
        for o in tools:
            xml_body += self.to_xml(o)        
        
        xml_body += "\n<!-- Glue records: agent to tools -->"
        for a in agent:
            for t in a.ai_tool_ids.mapped('ai_tool_id'):
                xml_body += f"""
        <record id="{a._name.replace('.', '_')}_{t._name.replace('.', '_')}" model="ai.agent.tool">
            <field name="ai_agent_id" ref="{self.external_id(module_name, a)}"/>
            <field name="ai_tool_id" ref="{self.external_id(module_name, t)}"/>
        </record>
        """

        xml_body += "\n<!-- Glue records: agent to memory -->"
        for a in agent:
            for m in a.ai_memory_ids.mapped('ai_memory_id'):
                xml_body += f"""
        <record id="{a._name.replace('.', '_')}_{m._name.replace('.', '_')}" model="ai.agent.memory">
            <field name="ai_agent_id" ref="{self.external_id(module_name, a)}"/>
            <field name="ai_memory_id" ref="{self.external_id(module_name, m)}"/>
        </record>
        """

        xml_body += "\n<!-- Glue records: quest to agent -->"
        for a in self.mapped('ai_agent_ids').mapped('ai_agent_id'):
            xml_body += f"""
        <record id="{self._name.replace('.', '_')}_{a._name.replace('.', '_')}" model="ai.quest.agent">
            <field name="ai_agent_id" ref="{self.external_id(module_name, a)}"/>
            <field name="ai_quest_id" ref="{self.external_id(module_name, self)}"/>
        </record>
        """

        raise UserError(xml_body)
        # Filstructure 
        files = {
            f'{module_name}/__init__.py': "#\n",
            f'{module_name}/data/ai_quest.xml':  f"""\
<?xml version="1.0" encoding="utf-8"?>\n<odoo>\n<data noupdate="1">
{xml_body}
</data>\n</odoo>
""",
            f'{module_name}/__manifest__.py': f"""\
{{
    'name': "{module_name.replace('_', ' ').title()}",
    'version': '1.0',
    'depends': ['ai_agent'],
    'author': "{author}",
    'category': 'Tools',
    'description': \"\"\"\n{description}\n\"\"\",
    'data': [],
    'installable': True,
    'application': False,
}}
""",
        }
        mem_zip = io.BytesIO()
        with zipfile.ZipFile(mem_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for path, content in files.items():
                zf.writestr(path, content)
        mem_zip.seek(0)
        zip_bytes = mem_zip.read()
        # Return as base64 for attachement or download
        return base64.b64encode(zip_bytes).decode('ascii')



                # ~ <button name="generate_simple_module"
                        # ~ type="object"
                        # ~ string="Ladda ner zip"
                        # ~ icon="fa-download"
                        # ~ class="btn-primary"/>



# ~ def action_download_zip(self):
    # ~ self.ensure_one()
    # ~ download_url = '/my_module/download_zip/%s' % self.id
    # ~ return {
        # ~ 'type': 'ir.actions.act_url',
        # ~ 'url': download_url,
        # ~ 'target': 'self',
    # ~ }


# ~ from odoo import http
# ~ from odoo.http import request

# ~ class MyModuleController(http.Controller):

    # ~ @http.route('/my_module/download_zip/<int:record_id>', type='http', auth='user')
    # ~ def download_zip(self, record_id, **kwargs):
        # ~ record = request.env['my.model'].browse(record_id)
        # ~ zip_bytes = record.generate_simple_module_zip(...)  # Din metod som skapar zip
        # ~ filename = 'my_module.zip'
        # ~ headers = [
            # ~ ('Content-Type', 'application/zip'),
            # ~ ('Content-Disposition', f'attachment; filename="{filename}"'),
        # ~ ]
        # ~ return request.make_response(zip_bytes, headers)
