from odoo.fields import Field
from operator import attrgetter
class Vector(Field):
    """Encapsulates a vector content (e.g. a token vector)."""
    
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

class Binary(Field):
    """Encapsulates a binary content (e.g. a file).

    :param bool attachment: whether the field should be stored as `ir_attachment`
        or in a column of the model's table (default: ``True``).
    """
    type = 'binary'

    prefetch = False                    # not prefetched by default
    _depends_context = ('bin_size',)    # depends on context (content or size)
    attachment = True                   # whether value is stored in attachment

    @property
    def column_type(self):
        return None if self.attachment else ('bytea', 'bytea')

    def _get_attrs(self, model_class, name):
        attrs = super()._get_attrs(model_class, name)
        if not attrs.get('store', True):
            attrs['attachment'] = False
        return attrs

    _description_attachment = property(attrgetter('attachment'))

    def convert_to_column(self, value, record, values=None, validate=True):
        # Binary values may be byte strings (python 2.6 byte array), but
        # the legacy OpenERP convention is to transfer and store binaries
        # as base64-encoded strings. The base64 string may be provided as a
        # unicode in some circumstances, hence the str() cast here.
        # This str() coercion will only work for pure ASCII unicode strings,
        # on purpose - non base64 data must be passed as a 8bit byte strings.
        if not value:
            return None
        # Detect if the binary content is an SVG for restricting its upload
        # only to system users.
        magic_bytes = {
            b'P',  # first 6 bits of '<' (0x3C) b64 encoded
            b'<',  # plaintext XML tag opening
        }
        if isinstance(value, str):
            value = value.encode()
        if value[:1] in magic_bytes:
            try:
                decoded_value = base64.b64decode(value.translate(None, delete=b'\r\n'), validate=True)
            except binascii.Error:
                decoded_value = value
            # Full mimetype detection
            if (guess_mimetype(decoded_value).startswith('image/svg') and
                    not record.env.is_system()):
                raise UserError(_("Only admins can upload SVG files."))
        if isinstance(value, bytes):
            return psycopg2.Binary(value)
        try:
            return psycopg2.Binary(str(value).encode('ascii'))
        except UnicodeEncodeError:
            raise UserError(_("ASCII characters are required for %s in %s") % (value, self.name))

    def convert_to_cache(self, value, record, validate=True):
        if isinstance(value, _BINARY):
            return bytes(value)
        if isinstance(value, str):
            # the cache must contain bytes or memoryview, but sometimes a string
            # is given when assigning a binary field (test `TestFileSeparator`)
            return value.encode()
        if isinstance(value, int) and \
                (record._context.get('bin_size') or
                 record._context.get('bin_size_' + self.name)):
            # If the client requests only the size of the field, we return that
            # instead of the content. Presumably a separate request will be done
            # to read the actual content, if necessary.
            value = human_size(value)
            # human_size can return False (-> None) or a string (-> encoded)
            return value.encode() if value else None
        return None if value is False else value

    def convert_to_record(self, value, record):
        if isinstance(value, _BINARY):
            return bytes(value)
        return False if value is None else value

    def compute_value(self, records):
        bin_size_name = 'bin_size_' + self.name
        if records.env.context.get('bin_size') or records.env.context.get(bin_size_name):
            # always compute without bin_size
            records_no_bin_size = records.with_context(**{'bin_size': False, bin_size_name: False})
            super().compute_value(records_no_bin_size)
            # manually update the bin_size cache
            cache = records.env.cache
            for record_no_bin_size, record in zip(records_no_bin_size, records):
                try:
                    value = cache.get(record_no_bin_size, self)
                    try:
                        value = base64.b64decode(value)
                    except (TypeError, binascii.Error):
                        pass
                    try:
                        if isinstance(value, (bytes, _BINARY)):
                            value = human_size(len(value))
                    except (TypeError):
                        pass
                    cache_value = self.convert_to_cache(value, record)
                    dirty = self.column_type and self.store and any(records._ids)
                    cache.set(record, self, cache_value, dirty=dirty)
                except CacheMiss:
                    pass
        else:
            super().compute_value(records)

    def read(self, records):
        # values are stored in attachments, retrieve them
        assert self.attachment
        domain = [
            ('res_model', '=', records._name),
            ('res_field', '=', self.name),
            ('res_id', 'in', records.ids),
        ]
        # Note: the 'bin_size' flag is handled by the field 'datas' itself
        data = {
            att.res_id: att.datas
            for att in records.env['ir.attachment'].sudo().search(domain)
        }
        records.env.cache.insert_missing(records, self, map(data.get, records._ids))

    def create(self, record_values):
        assert self.attachment
        if not record_values:
            return
        # create the attachments that store the values
        env = record_values[0][0].env
        with env.norecompute():
            env['ir.attachment'].sudo().with_context(
                binary_field_real_user=env.user,
            ).create([{
                    'name': self.name,
                    'res_model': self.model_name,
                    'res_field': self.name,
                    'res_id': record.id,
                    'type': 'binary',
                    'datas': value,
                }
                for record, value in record_values
                if value
            ])

    def write(self, records, value):
        if not self.attachment:
            return super().write(records, value)

        # discard recomputation of self on records
        records.env.remove_to_compute(self, records)

        # update the cache, and discard the records that are not modified
        cache = records.env.cache
        cache_value = self.convert_to_cache(value, records)
        records = cache.get_records_different_from(records, self, cache_value)
        if not records:
            return records
        if self.store:
            # determine records that are known to be not null
            not_null = cache.get_records_different_from(records, self, None)

        cache.update(records, self, itertools.repeat(cache_value))

        # retrieve the attachments that store the values, and adapt them
        if self.store and any(records._ids):
            real_records = records.filtered('id')
            atts = records.env['ir.attachment'].sudo()
            if not_null:
                atts = atts.search([
                    ('res_model', '=', self.model_name),
                    ('res_field', '=', self.name),
                    ('res_id', 'in', real_records.ids),
                ])
            if value:
                # update the existing attachments
                atts.write({'datas': value})
                atts_records = records.browse(atts.mapped('res_id'))
                # create the missing attachments
                missing = (real_records - atts_records)
                if missing:
                    atts.create([{
                            'name': self.name,
                            'res_model': record._name,
                            'res_field': self.name,
                            'res_id': record.id,
                            'type': 'binary',
                            'datas': value,
                        }
                        for record in missing
                    ])
            else:
                atts.unlink()

        return records
