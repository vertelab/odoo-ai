from odoo.fields import Field
class Vector(Field):
    """Field class in order to get all the basic features of an odoo      field"""
    type = 'vector'
    column_type = ('type', 'type')
    def convert_to_column(self, value, record, values=None, validate=True):
        # Converting to column in order to save the data
        return value
    def convert_to_record(self, value, record):
        # Converting to column record in order to handle the data
        return value or None
    def convert_to_read(self, value, record, use_name_get=True):
        # Converts the data to a readable format
        return value
    def convert_to_export(self, value, record):
        # Converting the data to Excel or csv compatible
        if value or value == "":
            return value
        return ''

