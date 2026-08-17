# Makefile för pi-agent — fristående CLI i ai_agent_core/bin

PYTHON ?= python3
TARGET  = pi-agent

.PHONY: all test lint install uninstall clean

all: lint test

## Syntax + enhetstester
test:
	$(PYTHON) -m py_compile $(TARGET)
	$(PYTHON) -m unittest discover -s tests -p "test_pi_agent*.py" -v

## Lint (pyflakes om tillgängligt)
lint:
	$(PYTHON) -m pyflakes $(TARGET) 2>/dev/null || \
	  $(PYTHON) -c "import ast; ast.parse(open('$(TARGET)').read()); print('lint OK (syntax)')"

## Installera som /usr/local/bin/pi-agent (kräver sudo)
install:
	install -m 0755 $(TARGET) /usr/local/bin/pi-agent
	@echo "installerad: /usr/local/bin/pi-agent"

## Avinstallera
uninstall:
	rm -f /usr/local/bin/pi-agent

## Visa verktygskatalog (lazy discovery)
tools:
	$(PYTHON) $(TARGET) --list-tools

clean:
	rm -rf __pycache__ tests/__pycache__
