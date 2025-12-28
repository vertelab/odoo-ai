# AGENTS

## Commands
- Install dependencies: sudo pip3 install -r requirements.txt --ignore-installed --break-system-packages
- Run all tests: python3 -m unittest discover -s ai_agent/tests
- Run a single test: python3 -m unittest ai_agent.tests.test_llm.TestLLM.test_get_llm

## Style Guidelines
- PEP8: 4-space indent, max line length 160
- Imports order: stdlib > 3rd-party > Odoo modules > local modules
- Long imports: use backslashes or parentheses
- Strings: double quotes
- Type hints: use typing.Annotated, List, TypedDict
- Naming: snake_case for funcs/vars, CamelCase for classes
- Odoo models: class ModelName(models.Model), file snake_case
- Error handling: user-facing via raise UserError; internal via _logger + traceback
- XML: record ids lowercase_underscore; attributes alphabetical

## Cursor/Copilot rules
No .cursor or Copilot instructions found
