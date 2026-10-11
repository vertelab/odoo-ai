"""Guard against the _cr_holder scope bug in the SSE stream controller.

The bug: _cr_holder was assigned inside the nested _stream() function, but
read in generate() (the enclosing function). Python then resolves
_cr_holder as a GLOBAL in generate(), which is never set, so every streaming
request raised NameError and returned an error event to the client.

These tests pin the binding so the mistake cannot come back.
"""
import ast
import symtable

import odoo.modules.module
from odoo.tests.common import BaseCase


def _generate_source():
    """Source of the nested generate() inside AIStreamController.stream().

    generate() is nested inside the stream() method, so it is not reachable
    as an attribute. Extract it from the parsed module source instead.
    """
    tree = ast.parse(_module_source())

    def find(node, path):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if path == ['AIStreamController', 'stream'] and child.name == 'generate':
                    return child
                hit = find(child, path + [child.name])
                if hit is not None:
                    return hit
            elif isinstance(child, ast.ClassDef):
                hit = find(child, path + [child.name])
                if hit is not None:
                    return hit
        return None

    node = find(tree, [])
    if node is None:
        raise AssertionError(
            'generate() hittades inte i AIStreamController.stream()')
    return ast.get_source_segment(_module_source(), node)


def _module_source():
    """Full controller source - symtable needs the whole file to resolve scopes."""
    path = odoo.modules.module.get_module_path('ai_agent_core')
    with open(f'{path}/controllers/stream.py') as fh:
        return fh.read()


class TestStreamScopeBindings(BaseCase):

    def test_cr_holder_is_assigned_in_generate(self):
        """_cr_holder must be created in generate()'s own scope."""
        tree = ast.parse(_generate_source())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'generate':
                assigned = {
                    target.id
                    for stmt in node.body
                    if isinstance(stmt, ast.Assign)
                    for target in stmt.targets
                    if isinstance(target, ast.Name)
                }
                self.assertIn(
                    '_cr_holder', assigned,
                    "_cr_holder måste skapas i generate()-scopet, inte i _stream()")
                return
        self.fail("generate() hittades inte i källkoden")

    def test_cr_holder_not_assigned_in_stream(self):
        """_stream() must NOT assign _cr_holder - that makes it local there."""
        tree = ast.parse(_generate_source())
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == '_stream':
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Assign):
                        for target in sub.targets:
                            if isinstance(target, ast.Name):
                                self.assertNotEqual(
                                    target.id, '_cr_holder',
                                    "_cr_holder får inte tilldelas i _stream() - "
                                    "då blir den lokal och generate() ser den inte")
                return
        self.fail("_stream() hittades inte i källkoden")

    def test_cr_holder_is_filled_by_generate(self):
        """generate() must fill the holder - the checkpoint callback reads it."""
        self.assertIn(
            "_cr_holder['cr'] = gen_cr", _generate_source(),
            "generate() måste fylla _cr_holder med cursorn")

    def test_static_scope_resolution(self):
        """The real proof: local in generate, free (closure) in _stream."""
        table = symtable.symtable(_module_source(), 'stream.py', 'exec')

        found = {}

        def walk(tbl, path=''):
            for sym in tbl.get_symbols():
                if sym.get_name() == '_cr_holder':
                    found[f'{path}{tbl.get_name()}'] = sym
            for child in tbl.get_children():
                walk(child, f'{path}{tbl.get_name()}.')

        walk(table)
        self.assertTrue(found, '_cr_holder hittades inte i symboltabellen')

        generate_scopes = {k: v for k, v in found.items() if k.endswith('generate')}
        stream_scopes = {k: v for k, v in found.items() if k.endswith('_stream')}

        self.assertTrue(generate_scopes, 'generate-scope saknas i symboltabellen')
        self.assertTrue(stream_scopes, '_stream-scope saknas i symboltabellen')

        for scope, sym in generate_scopes.items():
            self.assertTrue(
                sym.is_local(),
                f'{scope}: _cr_holder måste vara lokal i generate() - '
                f'om den blir global uppstår NameError vid varje streamanrop')
        for scope, sym in stream_scopes.items():
            self.assertTrue(
                sym.is_free(),
                f'{scope}: _cr_holder måste vara en fri variabel i _stream() '
                f'(stängd över från generate())')
