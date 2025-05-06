from lxml import etree
from odoo import models, api
from odoo.exceptions import UserError
import base64
import io
import logging
import re
import zipfile

_logger = logging.getLogger(__name__)

class AiQuest(models.Model):
    _inherit = 'ai.quest'
    
    def external_id(self,module,object):
        xml_id = object.get_external_id().get(object.id)
        if not xml_id:
            xml_id = f"{module}.{object._name.replace('.', '_')}_{object.id}"
        return xml_id.replace('new.','')
 
    def to_xml(self,module,record):
        """Return XML-data for record."""
        ext_id = self.external_id(module,record)
        m,r = ext_id.split('.')
        if m != module:
            return f"\n<!-- not exported {ext_id} -->\n"
        record_elem = etree.Element('record', model=record._name, id=ext_id)        
        for field in record._fields:
            if field in ['id','display_name','create_date','create_uid','write_date','write_uid','last_run',]:
                continue
            if record._fields[field].type in ('one2many', 'many2many','many2one','related'):
                continue                
            value = getattr(record, field)
            if field == 'memory_faiss' and value: # Large file
                etree.SubElement(record_elem,'field',name=field,type="base64",file=f"{module}/files/{record.name}.faiss")
                continue
            if field == 'memory_markdown' and value: # Large file
                etree.SubElement(record_elem,'field',name=field,type="base64",file=f"{module}/files/{record.name}.markdown")
                continue
            if value and not record._fields[field].type in ('one2many', 'many2many','many2one') and not record._fields[field].compute:
                field_elem = etree.SubElement(record_elem, 'field', name=field)
                if record._fields[field].type in ('text','html'):
                    field_elem.text = etree.CDATA(str(value))
                else:
                    field_elem.text = str(value)
        return "\n" + etree.tostring(record_elem, pretty_print=True, encoding='unicode') + "\n\n"
    
    @api.model
    def generate_simple_module(self, module_name):
        attachments =  []
        xml_body = "\n<!-- Full records: Quest, agent memory and tools -->\n"
        obj = [self]
        for a in self.ai_agent_ids.mapped('ai_agent_id'):
            obj.append(a)
            obj.extend(list(a.ai_memory_ids.mapped('ai_memory_id')))
            obj.extend([t.ai_tool_id for t in a.ai_tool_ids])
        for o in list(set(obj)):
            xml_body += self.to_xml(module_name,o)        
        
        for a in self.ai_agent_ids.mapped('ai_agent_id'):
            xml_body += "\n<!-- Glue records: agent to tools -->\n"
            for t in a.ai_tool_ids.mapped('ai_tool_id'):
                xml_body += f"""
        <record id="{a._name.replace('.', '_')}_{t._name.replace('.', '_')}_{t.id}" model="ai.agent.tool">
            <field name="ai_agent_id" ref="{self.external_id(module_name, a)}"/>
            <field name="ai_tool_id" ref="{self.external_id(module_name, t)}"/>
        </record>
        """.strip()

            xml_body += "\n<!-- Glue records: agent to memory -->\n"
            for m in a.ai_memory_ids.mapped('ai_memory_id'):
                xml_body += f"""
        <record id="{a._name.replace('.', '_')}_{m._name.replace('.', '_')}_{m.id}" model="ai.agent.memory">
            <field name="ai_agent_id" ref="{self.external_id(module_name, a)}"/>
            <field name="ai_memory_id" ref="{self.external_id(module_name, m)}"/>
        </record>\n
        """.strip() + "\n"
                for att in self.env['ir.attachment'].search([
                                        ('res_model', '=', m._name),
                                        ('res_id', '=', m.id),
                                ]):
                        # Skapa ett unikt id för bilagan
                        att_xml_id = f"{module_name}_attachment_{att.id}"
                        # Lägg till bilagan i xml_body (datas är base64)
                        xml_body += "\n" + f"""\n<record id="{att_xml_id}" model="ir.attachment">
        <field name="name">{att.name}</field>
        <field name="type">{att.type}</field>
        <field name="datas" type="base64" file="{module_name}/files/{att.name}"/>
        <field name="res_model">{att.res_model}</field>
        <field name="res_id" ref="{self.external_id(module_name, m)}"/>
        <field name="mimetype">{att.mimetype or ''}</field>
    </record>

    """.strip() + "\n"
                        attachments.append((f"{module_name}/files/{att.name}",base64.b64decode(att.datas)))
                if m.memory_faiss:
                    attachments.append((f"{module_name}/files/{m.name}.faiss",base64.b64decode(m.memory_faiss)))
                if m.memory_markdown:
                    attachments.append((f"{module_name}/files/{m.name}.markdown",base64.b64decode(m.memory_markdown)))
                                
            xml_body += "\n<!-- Glue records: quest to agent -->\n"
            for a in self.mapped('ai_agent_ids'):
                xml_body += f"""
        <record id="{self._name.replace('.', '_')}_{a.ai_agent_id._name.replace('.', '_')}_{a.ai_agent_id.id}" model="ai.quest.agent">
            <field name="ai_agent_id" ref="{self.external_id(module_name, a.ai_agent_id)}"/>
            <field name="sequence">{a.sequence}</field>
            <field name="ai_quest_id" ref="{self.external_id(module_name, self)}"/>
        </record>
        """.strip() + "\n"

        # Filestructure 
        files = {
            f'{module_name}/__init__.py': "#\n",
            f'{module_name}/data/ai_quest.xml':  f"""\
<?xml version="1.0" encoding="utf-8"?>\n<odoo>\n<data>
{xml_body}
</data>\n</odoo>
""",
            f'{module_name}/__manifest__.py': f"""\
{{
    'name': "{module_name.replace('_', ' ').title()}",
    'version': '1.0',
    'depends': ['ai_agent'],
    'author': "{self.env.company.name}",
    'category': 'Tools',
    'description': \"\"\"\nQuest {self.name}\n\"\"\",
    'data': ['data/ai_quest.xml'],
    'installable': True,
    'application': False,
}}
""",
        }
        for att in attachments:
            files[att[0]]=att[1]
        mem_zip = io.BytesIO()
        with zipfile.ZipFile(mem_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for path, content in files.items():
                zf.writestr(path, content)
        mem_zip.seek(0)
        zip_bytes = mem_zip.read()
        # Return as base64 for a attachement
        return base64.b64encode(zip_bytes).decode('ascii')
