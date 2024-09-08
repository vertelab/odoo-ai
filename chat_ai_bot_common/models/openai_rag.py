import json
import logging
import base64
import numpy as np
from sentence_transformers import SentenceTransformer


from odoo import models, fields, api, _
from odoo.exceptions import MissingError, AccessError, UserError

_logger = logging.getLogger(__name__)



class OdooRAG(models.Model):
    _name = 'odoo.rag'
    _description = 'Odoo RAG System'





class OpenAIRAG(models.Model):
    _name = 'openai.rag'
    _description = 'Storing small sets of vector data'
    channel_id = fields.Many2one(comodel_name='discuss.channel', string="Channel", help="")  #
    name = fields.Char(required=True)
    rag_data = fields.Text(string='RAG Data', help='Serialized RAG data')
    type = fields.Char(string='Type',required=True)

    @api.model
    def create_rag_odoo_models(self,type,name="Odoo Models RAG"):
        # Initialize the sentence transformer model
        model = SentenceTransformer('all-MiniLM-L6-v2')

        # Extract model and field information
        ir_model = self.env['ir.model']
        models_data = []
        for odoo_model in ir_model.search([]):
            model_info = f"Model: {odoo_model.name} ({odoo_model.model})"
            fields_info = []
            for field in odoo_model.field_id:
                field_info = f"Field: {field.name} ({field.ttype})"
                fields_info.append(field_info)
            
            models_data.append(model_info + "\n" + "\n".join(fields_info))

        # Create embeddings
        embeddings = model.encode(models_data)

        # Serialize the embeddings
        serialized_data = {
            'embeddings': base64.b64encode(embeddings.tobytes()).decode('utf-8'),
            'shape': embeddings.shape
        }

        # Create a record in the odoo.rag model
        self.create({
            'name': self.name,
            'rag_data': json.dumps(serialized_data)
        })

    @api.model
    def load_rag(self,name="Odoo Models RAG"):
        rag_record = self.search([('name', '=', name)], limit=1)
        if rag_record:
            serialized_data = json.loads(rag_record.rag_data)
            embeddings = np.frombuffer(base64.b64decode(serialized_data['embeddings']), dtype=np.float32).reshape(serialized_data['shape'])
            return embeddings
        return None
        
class OpenAITrain(models.Model):
    _name = 'openai.train'
    _description = 'Storing training data'
    type = fields.Selection(selection=[('dll','DDL'),('code','Odoo Code'),('question','Question'),('doc','Documentation')],string='Type')
    rag_id = fields.Many2one(comodel_name='openai.rag',) # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate  name = fields.Char(required=True)
    json = fields.Text(string='Json Data', help='Json-data')

    @api.model
    def create_rag_odoo_models(self,rag_id):
        # Initialize the sentence transformer model
        model = SentenceTransformer('all-MiniLM-L6-v2')

        # Extract model and field information
        ir_model = self.env['ir.model']
        models_data = []
        for odoo_model in ir_model.search([]):
            model_info = f"Model: {odoo_model.name} ({odoo_model.model})"
            fields_info = []
            for field in odoo_model.field_id:
                field_info = f"Field: {field.name} ({field.ttype})"
                fields_info.append(field_info)
            
            models_data.append(model_info + "\n" + "\n".join(fields_info))

        # Create embeddings
        embeddings = model.encode(models_data)

        # Serialize the embeddings
        serialized_data = {
            'embeddings': base64.b64encode(embeddings.tobytes()).decode('utf-8'),
            'shape': embeddings.shape
        }

        # Create a record in the odoo.rag model
        self.create({
            'name': self.name,
            'rag_data': json.dumps(serialized_data)
        })

    @api.model
    def load_rag(self,rag_id):
        rag_record = self.search([('name', '=', name)], limit=1)
        if rag_record:
            serialized_data = json.loads(rag_record.rag_data)
            embeddings = np.frombuffer(base64.b64decode(serialized_data['embeddings']), dtype=np.float32).reshape(serialized_data['shape'])
            return embeddings
        return None
        
    @api.model
    def train(self,rag_id,question=None, ddl=None, documentation=None,code=None) 
        if question:
            self.create({'rag_id': rag_id, 'type': 'question', 'json': question})
        if ddl:
            self.create({'rag_id': rag_id, 'type': 'ddl', 'json': ddl})
        if documentation:
            self.create({'rag_id': rag_id, 'type': 'ddl', 'json': documentation})
        if code:
            self.create({'rag_id': rag_id, 'type': 'code', 'json': code})

