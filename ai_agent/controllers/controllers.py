# Part of Odoo. See LICENSE file for full copyright and licensing details.
import contextlib
import io
import json
import logging
import re
import time
import requests
import uuid
import werkzeug.exceptions
import werkzeug.urls
from PIL import Image, ImageFont, ImageDraw
from lxml import etree
from base64 import b64decode, b64encode
from datetime import datetime
from math import floor
from os.path import join as opj

from odoo.http import request, Response
from odoo import http, tools, _, SUPERUSER_ID, release
from odoo.addons.http_routing.models.ir_http import slug, unslug
from odoo.addons.web_editor.tools import get_video_url_data
from odoo.exceptions import UserError, MissingError, AccessError
from odoo.tools.misc import file_open
from odoo.tools.mimetypes import guess_mimetype
from odoo.tools.image import image_data_uri, binary_to_image
from odoo.addons.iap.tools import iap_tools
from odoo.addons.base.models.assetsbundle import AssetsBundle


