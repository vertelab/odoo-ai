# -*- coding: utf-8 -*-
from . import controllers

# Models are loaded by Odoo's module system when running inside Odoo.
# We try-import for standalone unit test compatibility.
try:
    from . import models
except AssertionError:
    pass  # Not running inside Odoo — models loaded separately
