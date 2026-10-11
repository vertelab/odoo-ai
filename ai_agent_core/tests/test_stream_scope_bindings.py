"""Guard against the _cr_holder scope bug in the SSE stream controller.

The bug: _cr_holder was assigned inside the nested _stream() function, but
read in generate() (the enclosing function). Python then resolves
_cr_holder as a GLOBAL in generate(), which is never set, so every streaming
request raised NameError and returned an error event to the client.

These tests pin the binding so the mistake cannot come back.

The same pattern has now bitten the file three times:

  * `_dsml` (2026-09-22) — "annars blir dsml_hold en lokal i _stream() och
    första läsningen kastar UnboundLocalError".
  * `_thread_holder` (session-history-relevance 3.5).
  * `_cr_holder` (2026-10-11) — the one that reached production.

All three are holders shared between `generate()` and its nested `_stream()`.
They must be created in `generate()`'s scope: assigning them in `_stream()`
makes them local there and invisible to `generate()`.

The scope chain is three levels deep, and the test must know all three:

    stream()          <- outer: closure for both nested functions
      generate()      <- middle: closure for _stream, LOCAL to itself
        _stream()     <- inner: everything bound here is invisible to generate()

Both SSE paths have the pattern, so both are checked:

  * AIStreamController.stream      (the web UI, /ai/stream)
  * AIOpenAIAPI._run_coworker_chat (the external OpenAI path, /ai/v1/...)

A static test is the right tool here. The failure only appears in the real
request path after request teardown, and the outer `except Exception` in
generate() turned it into an SSE `error` event rather than a traceback — an
integration test would need a live provider, a real cursor and an SSE client,
and could hide the bug behind the same except. symtable resolves the binding
regardless of how the code runs.
"""
import ast
import symtable

import odoo.modules.module
from odoo.tests.common import BaseCase, tagged


#: Holders shared between generate() and its nested _stream(). Each must be
#: created in generate()'s scope — never in _stream().
SHARED_HOLDERS = ('_cr_holder', '_thread_holder', '_dsml')

#: The SSE paths carrying the generate() -> _stream() pattern.
SSE_PATHS = (
    ('AIStreamController', 'stream'),
    ('AIOpenAIAPI', '_run_coworker_chat'),
)


def _module_source():
    """Full controller source - symtable needs the whole file to resolve scopes."""
    path = odoo.modules.module.get_module_path('ai_agent_core')
    with open(f'{path}/controllers/stream.py') as fh:
        return fh.read()


def _find_method(tree, cls_name, meth_name):
    """The method node for `cls_name.meth_name`, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == meth_name):
                    return item
    return None


def _nested(node, name):
    """First nested function called `name` inside `node`, or None.

    Recursive but stops at function boundaries: `generate()` in the
    openai_api path sits inside an if/else branch, so a direct-children
    scan would miss it.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name == name:
                return child
        else:
            hit = _nested(child, name)
            if hit is not None:
                return hit
    return None


def _generate_source(cls_name='AIStreamController', meth_name='stream'):
    """Source of the nested generate() inside the given SSE method.

    generate() is nested inside the method, so it is not reachable as an
    attribute. Extract it from the parsed module source instead.
    """
    src = _module_source()
    tree = ast.parse(src)
    method = _find_method(tree, cls_name, meth_name)
    if method is None:
        raise AssertionError(
            '%s.%s hittades inte i källkoden' % (cls_name, meth_name))
    node = _nested(method, 'generate')
    if node is None:
        raise AssertionError(
            'generate() hittades inte i %s.%s()' % (cls_name, meth_name))
    return ast.get_source_segment(src, node)


def _scope_symbols(source, name):
    """{scope path: symbol} for `name` across the whole module's scopes."""
    table = symtable.symtable(source, 'stream.py', 'exec')
    found = {}

    def walk(tbl, path=''):
        for sym in tbl.get_symbols():
            if sym.get_name() == name:
                found[f'{path}{tbl.get_name()}'] = sym
        for child in tbl.get_children():
            walk(child, f'{path}{tbl.get_name()}.')

    walk(table)
    return found


@tagged('post_install', '-at_install')
class TestStreamScopeBindings(BaseCase):
    """Pin the shared-holder binding in both SSE paths."""

    # ── 1. _cr_holder: the exact bug ─────────────────────────────────

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
        found = _scope_symbols(_module_source(), '_cr_holder')
        self.assertTrue(found, '_cr_holder hittades inte i symboltabellen')

        generate_scopes = {k: v for k, v in found.items()
                           if k.endswith('generate')}
        stream_scopes = {k: v for k, v in found.items()
                         if k.endswith('_stream')}

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

    # ── 2. The same pattern in BOTH SSE paths ────────────────────────

    def test_both_sse_paths_have_the_pattern(self):
        """Both SSE paths must expose a generate() -> _stream() pair.

        If a path is renamed or restructured, the holder checks below would
        silently stop covering it. This test fails loudly instead.
        """
        for cls_name, meth_name in SSE_PATHS:
            label = '%s.%s' % (cls_name, meth_name)
            src = _module_source()
            method = _find_method(ast.parse(src), cls_name, meth_name)
            self.assertIsNotNone(
                method, '%s hittades inte i källkoden' % label)
            gen = _nested(method, 'generate')
            self.assertIsNotNone(
                gen, '%s: generate() hittades inte' % label)
            stream = _nested(gen, '_stream')
            self.assertIsNotNone(
                stream, '%s: _stream() hittades inte i generate()' % label)

    def test_shared_holders_are_local_in_generate_not_in_stream(self):
        """Every shared holder: local in generate(), NOT local in _stream().

        This generalises the _cr_holder check to `_thread_holder` and
        `_dsml`, and covers BOTH SSE paths. `_dsml` lives in the openai_api
        path, which the single-path tests above never reach — that is the
        gap this test closes.
        """
        src = _module_source()
        checked = set()
        for holder in SHARED_HOLDERS:
            found = _scope_symbols(src, holder)
            if not found:
                continue  # holder not used in this revision — skip
            checked.add(holder)

            generate_scopes = {k: v for k, v in found.items()
                               if k.endswith('generate')}
            stream_scopes = {k: v for k, v in found.items()
                             if k.endswith('_stream')}

            self.assertTrue(
                generate_scopes,
                '%s: ingen generate-scope i symboltabellen — hållaren måste '
                'skapas i generate(), inte i _stream()' % holder)
            for scope, sym in generate_scopes.items():
                self.assertTrue(
                    sym.is_local(),
                    '%s i %s: måste vara lokal i generate() — annars blir '
                    'uppslaget globalt (NameError)' % (holder, scope))
            for scope, sym in stream_scopes.items():
                self.assertFalse(
                    sym.is_local(),
                    '%s i %s: får INTE vara lokal i _stream() — det är exakt '
                    'buggen (generate() ser den då inte)' % (holder, scope))

        self.assertTrue(
            checked,
            'ingen av de delade hållarna %s hittades — testet mäter inget'
            % (SHARED_HOLDERS,))

    def test_dsml_holder_is_reachable_from_generate(self):
        """`_dsml` (openai_api path) must be a closure, not a _stream local.

        Regression guard for the 2026-09-22 bug: the holder was assigned in
        `_stream()`, so the first read raised UnboundLocalError and killed
        the whole SSE stream.
        """
        found = _scope_symbols(_module_source(), '_dsml')
        if not found:
            self.skipTest('_dsml används inte i denna revision')
        for scope, sym in found.items():
            if scope.endswith('_stream'):
                self.assertFalse(
                    sym.is_local(),
                    '%s: _dsml får inte vara lokal i _stream() — '
                    'UnboundLocalError vid första läsningen' % scope)
