# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang, format_date
from collections import defaultdict
import logging

_logger = logging.getLogger(__name__)

class ResPartner(models.Model):
    _inherit = "res.partner"
    _description = "Res Partner"

    withholding_tax_ids = fields.Many2many('account.tax', string="Withholding Tax", store=True, domain="[('withholding_tax','=',True)]")
