# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Developer tools exposed over MCP.

Every tool is a method on ``ai_pi_mcp.dev_tools`` (which ``_inherit``s the
``mcp.tool.mixin``) decorated with ``@mcp_tool``. Each tool receives a single
``params`` dictionary and returns a JSON-serialisable value (or raises an
exception, which the controller turns into an MCP error result).
"""

import ast
import logging

from lxml import etree

from odoo import models
from odoo.exceptions import UserError
from odoo.service.model import get_public_method

from .mcp_tool import mcp_tool

_logger = logging.getLogger(__name__)

# Tools whose execution mutates state. Used by the controller to enforce a
# read-only scope before a write-capable tool is reached.
_WRITE_TOOLS = frozenset({
    'view_set_arch',
    'view_restore',
    'action_set_domain',
    'action_set_view_mode',
    'action_set_order',
    'module_install',
    'module_uninstall',
    'module_upgrade',
    'create_record',
    'update_record',
    'delete_record',
    'call_method',
})


class AiPiMcpDevTools(models.AbstractModel):
    _inherit = 'mcp.tool.mixin'
    _name = 'ai_pi_mcp.dev_tools'
    _description = 'ai_pi_mcp Developer Tools'

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @mcp_tool(
        name='list_models',
        description='List Odoo models matching an optional search term.',
        input_schema={
            'type': 'object',
            'properties': {
                'search': {'type': 'string', 'description': 'Optional substring filter on the model name.'},
            },
        },
    )
    def _tool_list_models(self, params):
        search = (params or {}).get('search') or ''
        models_ = self.env['ir.model'].sudo().search(
            [('model', 'like', search)], order='model', limit=500,
        ).mapped('model')
        return {'models': models_, 'count': len(models_)}

    @mcp_tool(
        name='describe_model',
        description='Describe a model: fields (name/type/readonly/computed/store/relation), '
                    'public methods and relations.',
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Technical model name, e.g. stock.warehouse.orderpoint.'},
            },
            'required': ['model'],
        },
    )
    def _tool_describe_model(self, params):
        model = (params or {}).get('model')
        if not model:
            raise UserError('model is required')
        if model not in self.env:
            raise UserError('Unknown model: %s' % model)
        model_obj = self.env[model]
        fields_ = []
        for name, field in model_obj.fields_get().items():
            fields_.append({
                'name': name,
                'type': field.get('type'),
                'readonly': field.get('readonly'),
                'required': field.get('required'),
                'store': field.get('store'),
                'relation': field.get('relation'),
                'string': field.get('string'),
                'help': field.get('help'),
                'selection': field.get('selection'),
            })
        return {
            'model': model,
            'display_name': model_obj._description,
            'fields': fields_,
            'field_count': len(fields_),
        }

    @mcp_tool(
        name='whoami',
        description='Report which Odoo user the MCP session is acting as and their admin/system status.',
        input_schema={'type': 'object', 'properties': {}},
    )
    def _tool_whoami(self, params):
        user = self.env.user
        return {
            'uid': user.id,
            'login': user.login,
            'name': user.name,
            'is_superuser': user._is_superuser(),
            'has_system_group': user.has_group('base.group_system'),
            'db': self.env.cr.dbname,
        }

    @mcp_tool(
        name='get_access_rights',
        description='Report the current user access rights on a model (create/read/write/unlink).',
        input_schema={
            'type': 'object',
            'properties': {'model': {'type': 'string'}},
            'required': ['model'],
        },
    )
    def _tool_get_access_rights(self, params):
        model = (params or {}).get('model')
        if not model or model not in self.env:
            raise UserError('Unknown model: %s' % model)
        model_obj = self.env[model]
        return {
            'model': model,
            'can_create': model_obj.check_access_rights('create', raise_exception=False),
            'can_read': model_obj.check_access_rights('read', raise_exception=False),
            'can_write': model_obj.check_access_rights('write', raise_exception=False),
            'can_unlink': model_obj.check_access_rights('unlink', raise_exception=False),
        }

    @mcp_tool(
        name='view_list',
        description='List views (ir.ui.view) for a model, optionally filtered by type.',
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string'},
                'type': {'type': 'string', 'enum': ['form', 'list', 'kanban', 'search', 'graph', 'pivot', 'calendar', 'cohort', 'gantt', 'activity']},
            },
            'required': ['model'],
        },
    )
    def _tool_view_list(self, params):
        model = (params or {}).get('model')
        vtype = (params or {}).get('type')
        if not model:
            raise UserError('model is required')
        domain = [('model', '=', model)]
        if vtype:
            domain.append(('type', '=', vtype))
        views = self.env['ir.ui.view'].sudo().search(domain, order='priority,id')
        return {
            'views': [{
                'id': v.id,
                'name': v.name,
                'type': v.type,
                'xml_id': v.get_external_id().get(v.id) or '',
                'inherit_id': v.inherit_id.id,
                'mode': v.mode,
                'priority': v.priority,
                'active': v.active,
            } for v in views],
            'count': len(views),
        }

    @mcp_tool(
        name='view_get_arch',
        description='Return the arch (XML) of a single view by id.',
        input_schema={
            'type': 'object',
            'properties': {
                'view_id': {'type': 'integer'},
            },
            'required': ['view_id'],
        },
    )
    def _tool_view_get_arch(self, params):
        view_id = (params or {}).get('view_id')
        if not view_id:
            raise UserError('view_id is required')
        view = self.env['ir.ui.view'].sudo().browse(view_id)
        if not view.exists():
            raise UserError('View not found: %s' % view_id)
        return {
            'view_id': view.id,
            'name': view.name,
            'model': view.model,
            'type': view.type,
            'inherit_id': view.inherit_id.id if view.inherit_id else None,
            'mode': view.mode,
            'arch': view.arch,
        }

    @mcp_tool(
        name='view_get_merged',
        description='Return the fully combined (effective) arch for a model+view type, '
                    'i.e. what the user actually sees after all inheritance is applied.',
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string'},
                'type': {'type': 'string', 'enum': ['form', 'list', 'kanban', 'search', 'graph', 'pivot', 'calendar']},
            },
            'required': ['model', 'type'],
        },
    )
    def _tool_view_get_merged(self, params):
        model = (params or {}).get('model')
        vtype = (params or {}).get('type')
        if not model or not vtype:
            raise UserError('model and type are required')
        if model not in self.env:
            raise UserError('Unknown model: %s' % model)
        # Resolve the effective arch the way Odoo does when rendering a view.
        try:
            arch = self.env[model].get_view(view_type=vtype).get('arch')
        except Exception as exc:
            raise UserError('Could not build merged view for %s/%s: %s'
                            % (model, vtype, exc))
        return {
            'model': model,
            'type': vtype,
            'arch': arch,
        }

    # ------------------------------------------------------------------
    # View editing (guarded with backup)
    # ------------------------------------------------------------------

    @mcp_tool(
        name='view_set_arch',
        description='Replace the arch XML of a view. The previous arch is backed up to an '
                    'ir.attachment before the change (unless backup=False) so it can be '
                    'restored with view_restore.',
        input_schema={
            'type': 'object',
            'properties': {
                'view_id': {'type': 'integer'},
                'arch': {'type': 'string', 'description': 'The new raw arch XML. For inherit views this is the xpath fragment.'},
                'backup': {'type': 'boolean', 'description': 'Back up the current arch first (default true).'},
            },
            'required': ['view_id', 'arch'],
        },
    )
    def _tool_view_set_arch(self, params):
        view_id = (params or {}).get('view_id')
        arch = (params or {}).get('arch')
        do_backup = params.get('backup', True)
        if not view_id or arch is None:
            raise UserError('view_id and arch are required')
        view = self.env['ir.ui.view'].sudo().browse(view_id)
        if not view.exists():
            raise UserError('View not found: %s' % view_id)
        # Sanity: ensure the arch is well-formed XML before we overwrite.
        try:
            etree.fromstring(('<?xml version="1.0"?>' + arch).encode('utf-8')
                             if not arch.lstrip().startswith('<?xml') else arch.encode('utf-8'))
        except etree.XMLSyntaxError as exc:
            raise UserError('Invalid XML arch: %s' % exc)
        old_arch = view.arch
        if do_backup:
            self._backup_view(view, old_arch)
        view.write({'arch': arch})
        return {
            'view_id': view.id,
            'name': view.name,
            'backed_up': do_backup,
            'result': 'arch updated',
        }

    @mcp_tool(
        name='view_restore',
        description='Restore the most recent backup of a view arch (from the last view_set_arch).',
        input_schema={
            'type': 'object',
            'properties': {'view_id': {'type': 'integer'}},
            'required': ['view_id'],
        },
    )
    def _tool_view_restore(self, params):
        view_id = (params or {}).get('view_id')
        if not view_id:
            raise UserError('view_id is required')
        view = self.env['ir.ui.view'].sudo().browse(view_id)
        if not view.exists():
            raise UserError('View not found: %s' % view_id)
        attachment = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'ir.ui.view'),
            ('res_id', '=', view.id),
            ('name', 'like', 'ai_pi_mcp backup%'),
        ], order='id desc', limit=1)
        if not attachment:
            raise UserError('No backup found for view %s' % view_id)
        old_arch = view.arch
        view.write({'arch': attachment.raw.decode('utf-8') if attachment.raw else ''})
        return {
            'view_id': view.id,
            'restored_from_attachment': attachment.id,
            'previous_arch': old_arch,
            'result': 'arch restored',
        }

    def _backup_view(self, view, arch):
        """Snapshot the current arch as an ir.attachment for later restore."""
        self.env['ir.attachment'].sudo().create({
            'name': 'ai_pi_mcp backup %s (view %s)' % (self.env.cr.now(), view.id),
            'type': 'binary',
            'mimetype': 'application/xml',
            'raw': arch.encode('utf-8') if arch else b'',
            'res_model': 'ir.ui.view',
            'res_id': view.id,
        })

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @mcp_tool(
        name='action_list',
        description='List ir.actions.act_window actions for a model.',
        input_schema={
            'type': 'object',
            'properties': {'model': {'type': 'string'}},
            'required': ['model'],
        },
    )
    def _tool_action_list(self, params):
        model = (params or {}).get('model')
        if not model:
            raise UserError('model is required')
        actions = self.env['ir.actions.act_window'].sudo().search(
            [('res_model', '=', model)], order='name')
        return {
            'actions': [{
                'id': a.id,
                'name': a.name,
                'view_mode': a.view_mode,
                'domain': a.domain or '',
                'context': a.context or '',
                'xml_id': a.get_external_id().get(a.id) or '',
            } for a in actions],
            'count': len(actions),
        }

    def _get_action(self, action_id):
        action = self.env['ir.actions.act_window'].sudo().browse(action_id)
        if not action.exists():
            raise UserError('Action not found: %s' % action_id)
        return action

    @mcp_tool(
        name='action_set_domain',
        description='Set the domain (as a string) on an ir.actions.act_window.',
        input_schema={
            'type': 'object',
            'properties': {
                'action_id': {'type': 'integer'},
                'domain': {'type': 'string', 'description': 'Domain as a Python literal string, e.g. [("state","=","draft")].'},
            },
            'required': ['action_id', 'domain'],
        },
    )
    def _tool_action_set_domain(self, params):
        action_id = (params or {}).get('action_id')
        domain = (params or {}).get('domain')
        if not action_id or domain is None:
            raise UserError('action_id and domain are required')
        action = self._get_action(action_id)
        action.write({'domain': domain})
        return {'action_id': action.id, 'result': 'domain updated'}

    @mcp_tool(
        name='action_set_view_mode',
        description='Set the view_mode list on an ir.actions.act_window (comma separated).',
        input_schema={
            'type': 'object',
            'properties': {
                'action_id': {'type': 'integer'},
                'view_mode': {'type': 'string', 'description': 'e.g. "list,form" or "kanban,form".'},
            },
            'required': ['action_id', 'view_mode'],
        },
    )
    def _tool_action_set_view_mode(self, params):
        action_id = (params or {}).get('action_id')
        view_mode = (params or {}).get('view_mode')
        if not action_id or not view_mode:
            raise UserError('action_id and view_mode are required')
        action = self._get_action(action_id)
        action.write({'view_mode': view_mode})
        return {'action_id': action.id, 'result': 'view_mode updated'}

    @mcp_tool(
        name='action_set_order',
        description='Set the default sort order on an ir.actions.act_window.',
        input_schema={
            'type': 'object',
            'properties': {
                'action_id': {'type': 'integer'},
                'order': {'type': 'string', 'description': 'e.g. "id desc" or "name".'},
            },
            'required': ['action_id', 'order'],
        },
    )
    def _tool_action_set_order(self, params):
        action_id = (params or {}).get('action_id')
        order = (params or {}).get('order')
        if not action_id or order is None:
            raise UserError('action_id and order are required')
        action = self._get_action(action_id)
        action.write({'order': order})
        return {'action_id': action.id, 'result': 'order updated'}

    # ------------------------------------------------------------------
    # Module management
    # ------------------------------------------------------------------

    @mcp_tool(
        name='module_list',
        description='List modules (ir.module.module), optionally filtered by name and state.',
        input_schema={
            'type': 'object',
            'properties': {
                'search': {'type': 'string'},
                'state': {'type': 'string', 'enum': ['installed', 'uninstalled', 'to upgrade', 'to install', 'to remove', 'uninstallable']},
            },
        },
    )
    def _tool_module_list(self, params):
        search = (params or {}).get('search') or ''
        state = (params or {}).get('state')
        domain = []
        if search:
            domain.append(('name', 'ilike', search))
        if state:
            domain.append(('state', '=', state))
        modules = self.env['ir.module.module'].sudo().search(
            domain, order='name', limit=500)
        return {
            'modules': [{
                'id': m.id,
                'name': m.name,
                'shortdesc': m.shortdesc,
                'state': m.state,
                'version': m.installed_version or m.latest_version,
                'author': m.author,
            } for m in modules],
            'count': len(modules),
        }

    def _get_module(self, name):
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', name)], limit=1)
        if not module:
            raise UserError('Module not found: %s' % name)
        return module

    @mcp_tool(
        name='module_install',
        description='Install a module immediately (state -> installed).',
        input_schema={
            'type': 'object',
            'properties': {'name': {'type': 'string'}},
            'required': ['name'],
        },
    )
    def _tool_module_install(self, params):
        name = (params or {}).get('name')
        if not name:
            raise UserError('name is required')
        module = self._get_module(name)
        if module.state != 'uninstalled':
            raise UserError('Module %s is not in uninstalled state (state=%s)' % (name, module.state))
        module.button_immediate_install()
        return {'name': name, 'result': 'install scheduled-completed', 'state': module.state}

    @mcp_tool(
        name='module_uninstall',
        description='Uninstall a module immediately.',
        input_schema={
            'type': 'object',
            'properties': {'name': {'type': 'string'}},
            'required': ['name'],
        },
    )
    def _tool_module_uninstall(self, params):
        name = (params or {}).get('name')
        if not name:
            raise UserError('name is required')
        module = self._get_module(name)
        if module.state != 'installed':
            raise UserError('Module %s is not installed (state=%s)' % (name, module.state))
        module.button_immediate_uninstall()
        return {'name': name, 'result': 'uninstalled', 'state': module.state}

    @mcp_tool(
        name='module_upgrade',
        description='Upgrade a module immediately (re-runs its update).',
        input_schema={
            'type': 'object',
            'properties': {'name': {'type': 'string'}},
            'required': ['name'],
        },
    )
    def _tool_module_upgrade(self, params):
        name = (params or {}).get('name')
        if not name:
            raise UserError('name is required')
        module = self._get_module(name)
        if module.state != 'installed':
            raise UserError('Module %s is not installed (state=%s)' % (name, module.state))
        module.button_immediate_upgrade()
        return {'name': name, 'result': 'upgraded', 'state': module.state}

    # ------------------------------------------------------------------
    # Record CRUD
    # ------------------------------------------------------------------

    @mcp_tool(
        name='search_read',
        description='Search and read records of a model.',
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string'},
                'domain': {'type': 'string', 'description': 'Domain as a Python literal, e.g. [("name","ilike","x")].'},
                'fields': {'type': 'array', 'items': {'type': 'string'}},
                'limit': {'type': 'integer', 'default': 20},
                'order': {'type': 'string'},
            },
            'required': ['model'],
        },
    )
    def _tool_search_read(self, params):
        model = (params or {}).get('model')
        if not model or model not in self.env:
            raise UserError('Unknown model: %s' % model)
        domain = self._parse_domain(params.get('domain') or '[]')
        fields_ = params.get('fields') or []
        limit = params.get('limit') or 20
        order = params.get('order')
        records = self.env[model].search(domain, limit=limit, order=order)
        data = records.read(fields_) if fields_ else records.read()
        return {'records': data, 'count': len(data)}

    @mcp_tool(
        name='count',
        description='Count records of a model matching a domain.',
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string'},
                'domain': {'type': 'string'},
            },
            'required': ['model'],
        },
    )
    def _tool_count(self, params):
        model = (params or {}).get('model')
        if not model or model not in self.env:
            raise UserError('Unknown model: %s' % model)
        domain = self._parse_domain(params.get('domain') or '[]')
        return {'model': model, 'count': self.env[model].search_count(domain)}

    @mcp_tool(
        name='read_records',
        description='Read specific records of a model by id.',
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string'},
                'ids': {'type': 'array', 'items': {'type': 'integer'}},
                'fields': {'type': 'array', 'items': {'type': 'string'}},
            },
            'required': ['model', 'ids'],
        },
    )
    def _tool_read_records(self, params):
        model = (params or {}).get('model')
        ids = (params or {}).get('ids') or []
        if not model or model not in self.env:
            raise UserError('Unknown model: %s' % model)
        records = self.env[model].browse(ids)
        fields_ = params.get('fields') or []
        data = records.read(fields_) if fields_ else records.read()
        return {'records': data, 'count': len(data)}

    @mcp_tool(
        name='create_record',
        description='Create a record of a model. Returns the new id and display_name.',
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string'},
                'values': {'type': 'object'},
            },
            'required': ['model', 'values'],
        },
    )
    def _tool_create_record(self, params):
        model = (params or {}).get('model')
        values = (params or {}).get('values') or {}
        if not model or model not in self.env:
            raise UserError('Unknown model: %s' % model)
        record = self.env[model].create(values)
        return {'id': record.id, 'display_name': record.display_name}

    @mcp_tool(
        name='update_record',
        description='Update fields on a record of a model.',
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string'},
                'id': {'type': 'integer'},
                'values': {'type': 'object'},
            },
            'required': ['model', 'id', 'values'],
        },
    )
    def _tool_update_record(self, params):
        model = (params or {}).get('model')
        rid = (params or {}).get('id')
        values = (params or {}).get('values') or {}
        if not model or model not in self.env:
            raise UserError('Unknown model: %s' % model)
        record = self.env[model].browse(rid)
        if not record.exists():
            raise UserError('Record not found: %s.%s' % (model, rid))
        record.write(values)
        return {'id': record.id, 'display_name': record.display_name, 'result': 'updated'}

    @mcp_tool(
        name='delete_record',
        description='Delete record(s) of a model. Destructive — use with care.',
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string'},
                'ids': {'type': 'array', 'items': {'type': 'integer'}},
            },
            'required': ['model', 'ids'],
        },
    )
    def _tool_delete_record(self, params):
        model = (params or {}).get('model')
        ids = (params or {}).get('ids') or []
        if not model or model not in self.env:
            raise UserError('Unknown model: %s' % model)
        records = self.env[model].browse(ids)
        if not records.exists():
            raise UserError('No records found: %s.%s' % (model, ids))
        names = records.mapped('display_name')
        records.unlink()
        return {'deleted': list(names), 'count': len(ids)}

    @mcp_tool(
        name='call_method',
        description='Call a PUBLIC business method on a record (e.g. action_confirm, button_immediate_install). '
                    'Private methods (leading underscore) are blocked.',
        input_schema={
            'type': 'object',
            'properties': {
                'model': {'type': 'string'},
                'id': {'type': 'integer'},
                'method': {'type': 'string'},
                'args': {'type': 'array', 'items': {}},
                'kwargs': {'type': 'object'},
            },
            'required': ['model', 'id', 'method'],
        },
    )
    def _tool_call_method(self, params):
        model = (params or {}).get('model')
        rid = (params or {}).get('id')
        method = (params or {}).get('method')
        args = (params or {}).get('args') or []
        kwargs = (params or {}).get('kwargs') or {}
        if not model or model not in self.env:
            raise UserError('Unknown model: %s' % model)
        if not method:
            raise UserError('method is required')
        if method.startswith('_'):
            raise UserError('Private method %s is not callable via MCP' % method)
        record = self.env[model].browse(rid)
        if not record.exists():
            raise UserError('Record not found: %s.%s' % (model, rid))
        if not hasattr(record, method):
            raise UserError('Model %s has no method %s' % (model, method))
        # Enforce that the target is a public method (no underscore), mirroring
        # Odoo's get_public_method convention.
        try:
            get_public_method(record, method)
        except AttributeError:
            raise UserError('Method %s is not a public callable method' % method)
        result = getattr(record, method)(*args, **kwargs)
        return {'result': result}

    def _parse_domain(self, domain_str):
        """Safely evaluate a Python-literal domain string into a list."""
        try:
            parsed = ast.literal_eval(domain_str)
        except (ValueError, SyntaxError) as exc:
            raise UserError('Invalid domain: %s' % exc)
        if not isinstance(parsed, list):
            raise UserError('Domain must be a list, got %s' % type(parsed).__name__)
        return parsed
