# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang, format_date
from collections import defaultdict
import logging
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from hashlib import sha256
from json import dumps
import logging
from markupsafe import Markup
from psycopg2 import OperationalError
import re
from textwrap import shorten
from unittest.mock import patch

from odoo import api, fields, models, _, Command
from odoo.addons.base.models.decimal_precision import DecimalPrecision
from odoo.addons.account.tools import format_structured_reference_iso
from odoo.exceptions import UserError, ValidationError, AccessError, RedirectWarning

_logger = logging.getLogger(__name__)


class AccountPayment(models.Model):
    _inherit = "account.payment"


    def get_api_amount(self,amt):
        self.amount = amt
        _logger.info('========================')
        _logger.info(amt)
        _logger.info(self.amount)

    @api.depends('partner_id', 'payment_type')
    def tax_type_withholding(self):
        for rec in self:
            taxes = False
            if rec.payment_type == "outbound":
                if rec.partner_id:
                    if rec.partner_id.withholding_tax_ids:
                        taxes = self.env["account.tax"].search(
                            [("type_tax_use", "=", "purchase"), ("id", "in", rec.partner_id.withholding_tax_ids.ids),("sales_withholding_tax", "=", False)])
                    else:
                        taxes = self.env["account.tax"].search(
                            [("type_tax_use", "=", "purchase"), ("withholding_tax", "=", True),("sales_withholding_tax", "=", False)])
                else:
                    taxes = self.env["account.tax"].search(
                        [("type_tax_use", "=", "purchase"), ("withholding_tax", "=", True),("sales_withholding_tax", "=", False)])
            elif rec.payment_type == "inbound":
                if rec.partner_id:
                    if rec.partner_id.withholding_tax_ids:
                        taxes = self.env["account.tax"].search(
                            [("type_tax_use", "=", "sale"), ("id", "in", rec.partner_id.withholding_tax_ids.ids),("sales_withholding_tax", "=", False)])
                    else:
                        taxes = self.env["account.tax"].search(
                            [("type_tax_use", "=", "sale"), ("withholding_tax", "=", True),("sales_withholding_tax", "=", False)])
                else:
                    taxes = self.env["account.tax"].search(
                        [("type_tax_use", "=", "sale"), ("withholding_tax", "=", True),("sales_withholding_tax", "=", False)])

            if taxes:
                rec.withholding_tax_ids = taxes.ids
            else:
                rec.withholding_tax_ids = False

    # def default_withholding_tax(self):
    #     if len(self.partner_id.withholding_tax_ids.ids) > 1:
    #         return self.partner_id.withholding_tax_ids[0].id
    #     elif len(self.partner_id.withholding_tax_ids.ids) == 1:
    #         return self.partner_id.withholding_tax_ids.id
    #     else:
    #         return False
    is_multi_deduction = fields.Boolean()

    # def _get_check_key_list(self):
    #     return ["name", "account_id"]
    #
    # def _get_update_key_list(self):
    #     return ["analytic_distribution"]

    def _update_vals_writeoff(self, write_off_line_vals, line_vals_list, check_keys, update_keys):
        for line_vals in line_vals_list:
            if all(
                line_vals[check_key] == write_off_line_vals[0][check_key]
                for check_key in check_keys
            ):
                for update_key in update_keys:
                    line_vals[update_key] = write_off_line_vals[0][update_key]
                break
    registered_partner = fields.Boolean(string="Registered Partner",store=True)

    # @api.depends('partner_id')
    # def compute_registered_partner(self):
    #     for rec in self:
    #         if rec.partner_id:
    #             rec.registered_partner = rec.partner_id.registered_partner
    #         else:
    #             rec.registered_partner = False

    withholding_tax_ids = fields.Many2many(
        "account.tax",
        "withholding_tax_ids_rel",
        string="Income Tax Wths",
        compute="tax_type_withholding",
        store=True,
        readonly=False,
    )
    sales_tax_ids = fields.Many2many(
        "account.tax",
        'sales_tax_ids_rel',
        string="Sales Tax Wth",
        store=True,
    )
    Withholding_sales_tax_ids = fields.Many2many(
        "account.tax",
        'Withholding_sales_tax_ids_rel',
        string="Sales Tax",
        store=True,
        domain="[('sales_withholding_tax','=',True)]"
    )
    withholding_tax_id = fields.Many2many(
        "account.tax","withholding_tax_id_rel",
        string="Income Tax Wth",
        domain="[('id','in',withholding_tax_ids)]",
        store=True,

    )
    sale_tax_ids = fields.Many2many(
        "account.tax",
        'sale_tax_ids_rel',
        string="Sales Tax",
        store=True,
    )
    lc_note = fields.Char(string="LC")
    retention_money_payable = fields.Float(string="Retention Money Payable")
    advance = fields.Float(string="Advance")
    amount_exclusive_sales_tax = fields.Float(string="Exclusive Sales Tax Amount", store=True)
    amount_inclusive_sales_tax = fields.Float(string="Inclusive Sales Tax Amount", store=True)
    sales_tax_amount = fields.Float(string="Sales Tax Amount", store=True,compute="compute_amount_inclusive_sales_tax")
    sales_tax_amount_withholding = fields.Float(string="Sales Tax Amount WHT", store=True,compute="compute_amount_inclusive_sales_tax")
    amount_withholding = fields.Float(string="Income Tax Amount WHT", store=True)
    is_wht_trx = fields.Boolean(string="Multiple Writeoff")
    is_pass_writeoff = fields.Boolean(
        string="is Pass Writeoff",
        help="pass write-off journal items with multi account",
    )
    tax_percent = fields.Float(string="Tax Percent")

    withholding_tax_account_id = fields.Many2one(
        "account.account",
        domain="[('account_id',in',withholding_tax_id.invoice_repartition_line_ids)]",
    )


    @api.onchange('sale_tax_ids')
    def compute_sales_tax_percent(self):
        for rec in self:
            if rec.sale_tax_ids:
                tax_percent = 0
                count = 0
                for r in rec.sale_tax_ids:
                    if r.amount != 0:
                        tax_percent += r.amount
                        count += 1
                if tax_percent != 0 and count != 0:
                    rec.tax_percent = tax_percent / count
                else:
                    rec.tax_percent = 0
            else:
                rec.tax_percent = 0
    

    @api.onchange('amount_inclusive_sales_tax','sales_tax_amount_withholding','amount_withholding','advance','retention_money_payable')
    def onchange_amount_inclusive_sales_tax(self):
        if self.amount_inclusive_sales_tax  and self.payment_type=='outbound':
            self.amount = self.amount_inclusive_sales_tax - self.sales_tax_amount_withholding - self.amount_withholding - self.advance - self.retention_money_payable
        else:
            _logger.info('-----------------------------------------------------------------')
            _logger.info(self.amount_inclusive_sales_tax)
            self.amount = self.amount_inclusive_sales_tax
        _logger.info(self.amount)
        _logger.info(self.amount)
        _logger.info(self.amount)
        _logger.info(self.amount)

    @api.depends('tax_percent','amount_inclusive_sales_tax','amount_exclusive_sales_tax','Withholding_sales_tax_ids','retention_money_payable','advance')
    def compute_amount_inclusive_sales_tax(self):
        for rec in self:
            x = 0

            for a in rec.sale_tax_ids:
                x += (a.amount / 100 * rec.amount_exclusive_sales_tax)
            if x:
                rec.sales_tax_amount = int(x+0.5)
            else:
                rec.sales_tax_amount = 0.0
            rec.amount_inclusive_sales_tax = rec.amount_exclusive_sales_tax + rec.sales_tax_amount
            if rec.Withholding_sales_tax_ids:
                sales_tax_amount_withholding = 0.0
                for sales_tax_id in rec.Withholding_sales_tax_ids:
                    sales_tax_amount_withholding += int((rec.amount_exclusive_sales_tax - rec.retention_money_payable - rec.advance) * (sales_tax_id.amount / 100)+0.5)
                rec.sales_tax_amount_withholding = sales_tax_amount_withholding
            else:
                rec.sales_tax_amount_withholding = 0.0

    # @api.depends('sales_tax_ids')
    # def compute_sales_tax(self):
    #     for rec in self:
    #         if rec.sales_tax_ids:
    #             rec.Withholding_sales_tax_ids = rec.sales_tax_ids.ids
    #         else:
    #             rec.Withholding_sales_tax_ids = False

    # @api.onchange('partner_id')
    # def oncahnge_partner_id_withholding_tax(self):
    #     if len(self.partner_id.withholding_tax_ids.ids) > 1:
    #         self.withholding_tax_id = self.partner_id.withholding_tax_ids[0].id
    #     elif len(self.partner_id.withholding_tax_ids.ids) == 1:
    #         self.withholding_tax_id = self.partner_id.withholding_tax_ids.id
    #     else:
    #         self.withholding_tax_id = False

    @api.onchange('withholding_tax_id', 'amount','retention_money_payable','advance')
    def _onchange_wth_tax_amount(self):
        if self.withholding_tax_id:
            x = []
            for rec in self.withholding_tax_id:
                if self.sales_tax_amount:
                    x.append(int((self.amount_inclusive_sales_tax - self.retention_money_payable - self.advance) * (rec.amount / 100)+0.5))

                elif self.amount_exclusive_sales_tax:
                    x.append(int((self.amount_exclusive_sales_tax - self.retention_money_payable - self.advance) * (rec.amount / 100)+0.5))
                else:
                    x.append(0)
            self.amount_withholding = sum(x)
        else:
            self.amount_withholding = 0

    # @api.onchange("is_internal_transfer")
    # def _onchange_product(self):
    #     if self.is_internal_transfer:
    #         self.is_wht_trx = False

    def _prepare_payment_display_name(self):
        """
        Hook method for inherit
        When you want to set a new name for payment, you can extend this method
        """
        return {
            "outbound-customer": _("Customer Reimbursement"),
            "inbound-customer": _("Customer Payment"),
            "outbound-supplier": _("Vendor Payment"),
            "inbound-supplier": _("Vendor Reimbursement"),
        }

    def _get_check_key_list(self):
        return ["name", "account_id"]

    def _get_update_key_list(self):
        return ["analytic_distribution"]

    # def _update_vals_writeoff(self, write_off_line_vals, line_vals_list, check_keys, update_keys):
    #     for line_vals in line_vals_list:
    #         if all(
    #             line_vals[check_key] == write_off_line_vals[check_key]
    #             for check_key in check_keys
    #         ):
    #             for update_key in update_keys:
    #                 line_vals[update_key] = write_off_line_vals[update_key]
    #             break

    # def _synchronize_from_moves(self, changed_fields):
    #     if any(rec.is_multi_deduction for rec in self):
    #         self = self.with_context(skip_account_move_synchronization=True)
    #     return super()._synchronize_from_moves(changed_fields)

    def write(self, vals):
        """Skip move synchronization when
        edit payment with multi deduction
        """
        if any(rec.is_multi_deduction for rec in self):
            self = self.with_context(skip_account_move_synchronization=True)
        return super().write(vals)

    def _prepare_move_line_default_vals(self, write_off_line_vals=None,force_balance=None):
        """Prepare the dictionary to create the default account.move.lines for the current payment.
        :param write_off_line_vals: Optional dictionary to create a write-off account.move.line easily containing:
            * amount:       The amount to be added to the counterpart amount.
            * name:         The label to set on the line.
            * account_id:   The account on which create the write-off.
        :return: A list of python dictionary to be passed to the account.move.line's 'create' method.
        """
        self._onchange_wth_tax_amount()
        write_off_line_val = {}
        write_off_line_val = write_off_line_vals or {}

        if isinstance(write_off_line_vals, list):
            if write_off_line_vals:
                write_off_line_val = write_off_line_vals[0] or write_off_line_vals or {}
        self.ensure_one()
        write_off_line_val = write_off_line_val or {}

        if not self.outstanding_account_id:
            raise UserError(
                _(
                    "You can't create a new payment without an outstanding payments/receipts account set either on the company or the %s payment method in the %s journal.",
                    self.payment_method_line_id.name,
                    self.journal_id.display_name,
                )
            )

        # Compute amounts.
        write_off_amount_currency = write_off_line_val.get("amount", 0.0)
        amount_withholding = 0
        if self.payment_type == "inbound":
            # Receive money.
            liquidity_amount_currency = self.amount_inclusive_sales_tax
            amount_withholding = self.amount_withholding
        elif self.payment_type == "outbound":
            # Send money.
            liquidity_amount_currency = -self.amount_inclusive_sales_tax
            amount_withholding = -self.amount_withholding
            write_off_amount_currency *= -1
        else:
            liquidity_amount_currency = write_off_amount_currency = 0.0

        write_off_balance = self.currency_id._convert(
            write_off_amount_currency,
            self.company_id.currency_id,
            self.company_id,
            self.date,
        )
        liquidity_balance = self.currency_id._convert(
            liquidity_amount_currency,
            self.company_id.currency_id,
            self.company_id,
            self.date,
        )
        counterpart_amount_currency = (
                -liquidity_amount_currency - write_off_amount_currency
        )
        counterpart_balance = -liquidity_balance - write_off_balance
        currency_id = self.currency_id.id

        # if self.is_internal_transfer:
        #     if self.payment_type == "inbound":
        #         liquidity_line_name = _("Transfer to %s", self.journal_id.name)
        #     else:  # payment.payment_type == 'outbound':
        #         liquidity_line_name = _("Transfer from %s", self.journal_id.name)
        # else:
        #     liquidity_line_name = self.payment_reference
        liquidity_line_name = ''.join(x[1] for x in self._get_aml_default_display_name_list())

        # Compute a default label to set on the journal items.

        payment_display_name = self._prepare_payment_display_name()

        default_line_name = ''.join(x[1] for x in self._get_aml_default_display_name_list())
        # self.env["account.move.line"]._get_default_line_name(
        #     _("Internal Transfer")
        #     if self.is_internal_transfer
        #     else payment_display_name["%s-%s" % (self.payment_type, self.partner_type)],
        #     self.amount,
        #     self.currency_id,
        #     self.date,
        #     partner=self.partner_id,
        # )
        line_vals_list = []
        # _logger.info('llllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllll')
        # _logger.info(liquidity_amount_currency)
        # _logger.info(counterpart_balance)
        # _logger.info('kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk')
        if self.withholding_tax_id:
            for withtholding in self.withholding_tax_id:
                withholding_tax_account_id = False
                for accc in withtholding.invoice_repartition_line_ids.filtered(lambda x:x.account_id != False):
                    if accc.account_id:
                        withholding_tax_account_id = accc.account_id.id
                amount_withholding_tax = 0
                if self.payment_type == "inbound":
                    # Receive money.
                    if self.sales_tax_amount:
                        amount = self.amount_inclusive_sales_tax - self.retention_money_payable - self.advance
                    else:
                        amount = self.amount_exclusive_sales_tax - self.retention_money_payable - self.advance
                    amount_withholding_tax = int((withtholding.amount * amount / 100)+0.5)
                elif self.payment_type == "outbound":
                    # Send money.
                    if self.sales_tax_amount:
                        amount = self.amount_inclusive_sales_tax - self.retention_money_payable - self.advance
                    else:
                        amount = self.amount_exclusive_sales_tax - self.retention_money_payable - self.advance

                    amount_withholding_tax = -(int((withtholding.amount * amount / 100)+0.5))
                if liquidity_amount_currency > 0.0:
                    if self.sales_tax_amount:
                        amount = self.amount_inclusive_sales_tax - self.retention_money_payable - self.advance
                    else:
                        amount = self.amount_exclusive_sales_tax - self.retention_money_payable - self.advance
                    liquidity_amount_currency = liquidity_amount_currency - int((withtholding.amount * amount / 100)+0.5)
                    liquidity_balance = liquidity_balance - int((withtholding.amount * amount / 100)+0.5)

                if liquidity_amount_currency < 0.0:
                    if self.sales_tax_amount:
                        amount = self.amount_inclusive_sales_tax - self.retention_money_payable - self.advance
                    else:
                        amount = self.amount_exclusive_sales_tax - self.retention_money_payable - self.advance
                    liquidity_amount_currency = liquidity_amount_currency + int((withtholding.amount * amount / 100)+0.5)
                    liquidity_balance = liquidity_balance + int((withtholding.amount * amount / 100)+0.5)
                # _logger.info('amount_withholding_tax')
                # _logger.info(amount_withholding_tax)
                # _logger.info(counterpart_balance)
                # _logger.info(liquidity_amount_currency)
                # _logger.info(liquidity_balance)
                line_vals_list.append({
                    "name": withtholding.name or self.payment_reference or liquidity_line_name or default_line_name,
                    "date_maturity": self.date,
                    "amount_currency": amount_withholding_tax,
                    "currency_id": currency_id,
                    "debit": amount_withholding_tax if amount_withholding_tax > 0.0 else 0.0,
                    "credit": -amount_withholding_tax if amount_withholding_tax < 0.0 else 0.0,
                    "partner_id": self.partner_id.id,
                    "account_id": withholding_tax_account_id,
                }
                )
            if liquidity_balance > 0.0:
                # liquidity_balance = liquidity_balance - self.sales_tax_amount
                counterpart_balance = counterpart_balance - self.sales_tax_amount_withholding
            else:
                # liquidity_balance = liquidity_balance + self.sales_tax_amount
                counterpart_balance = counterpart_balance + self.sales_tax_amount_withholding
            sales_tax_account = False
            for accc in self.Withholding_sales_tax_ids:
                for acccc in accc.invoice_repartition_line_ids.filtered(lambda x: x.account_id != False):
                    if acccc.account_id:
                        sales_tax_account = acccc.account_id.id
                sales_tax_amount = int(((self.amount_exclusive_sales_tax) * accc.amount / 100)+0.5)
                if liquidity_amount_currency > 0.0:
                    liquidity_amount_currency = liquidity_amount_currency - sales_tax_amount
                    liquidity_balance = liquidity_balance - sales_tax_amount

                if liquidity_amount_currency < 0.0:
                    liquidity_amount_currency = liquidity_amount_currency + sales_tax_amount
                    liquidity_balance = liquidity_balance + sales_tax_amount
                if liquidity_balance < 0.0:
                    sales_tax_amount = -sales_tax_amount
                if counterpart_balance > 0.0:
                    counterpart_balance += sales_tax_amount
                if counterpart_balance < 0.0:
                    counterpart_balance += sales_tax_amount

                # _logger.info('sales_tax_amount')
                # _logger.info(sales_tax_amount)
                # _logger.info(counterpart_balance)
                # _logger.info(liquidity_balance)
                # _logger.info(liquidity_amount_currency)
                line_vals_list.append(
                    {
                        "name": accc.name or liquidity_line_name or default_line_name,
                        "date_maturity": self.date,
                        "amount_currency": sales_tax_amount,
                        "currency_id": currency_id,
                        "debit": sales_tax_amount if sales_tax_amount > 0.0 else 0.0,
                        "credit": -sales_tax_amount if sales_tax_amount < 0.0 else 0.0,
                        "partner_id": self.partner_id.id,
                        "account_id": sales_tax_account,
                    }
                )

            if self.retention_money_payable:
                # _logger.info('self.retention_money_payable')
                # _logger.info(self.retention_money_payable)
                retention_money = self.retention_money_payable if liquidity_balance > 0.0 else -self.retention_money_payable
                liquidity_balance = liquidity_balance - retention_money if liquidity_balance > 0.0 else liquidity_balance - retention_money
                liquidity_amount_currency = liquidity_amount_currency - retention_money if liquidity_amount_currency > 0.0 else liquidity_amount_currency - retention_money
                retention_account_id = self.env['account.account'].search([('retention_money_payable','=',True)],limit=1)
                line_vals_list.append(
                    {
                        "name": retention_account_id.name or liquidity_line_name or default_line_name,
                        "date_maturity": self.date,
                        "amount_currency": retention_money,
                        "currency_id": currency_id,
                        "debit": retention_money if retention_money > 0.0 else 0.0,
                        "credit": -retention_money if retention_money < 0.0 else 0.0,
                        "partner_id": self.partner_id.id,
                        "account_id": retention_account_id.id,
                    }
                )
            if self.advance:
                # _logger.info('self.advance')
                # _logger.info(self.advance)
                advance = self.advance if liquidity_balance > 0.0 else -self.advance
                liquidity_balance = liquidity_balance - advance if liquidity_balance > 0.0 else liquidity_balance - advance
                liquidity_amount_currency = liquidity_amount_currency - advance if liquidity_amount_currency > 0.0 else liquidity_amount_currency - advance
                advance_account_id = self.env['account.account'].search([('advance','=',True)],limit=1)
                line_vals_list.append(
                    {
                        "name": advance_account_id.name or liquidity_line_name or default_line_name,
                        "date_maturity": self.date,
                        "amount_currency": advance,
                        "currency_id": currency_id,
                        "debit": advance if advance > 0.0 else 0.0,
                        "credit": -advance if advance < 0.0 else 0.0,
                        "partner_id": self.partner_id.id,
                        "account_id": advance_account_id.id,
                    }
                )
            # _logger.info('liquidity_amount_currency')
            # _logger.info(liquidity_amount_currency)
            line_vals_list.append(
                {
                    "name": liquidity_line_name or default_line_name,
                    "date_maturity": self.date,
                    "amount_currency": liquidity_amount_currency,
                    "currency_id": currency_id,
                    "debit": liquidity_balance if liquidity_balance > 0.0 else 0.0,
                    "credit": -liquidity_balance if liquidity_balance < 0.0 else 0.0,
                    "partner_id": self.partner_id.id,
                    "account_id": self.outstanding_account_id.id,
                }
            )

        else:
            sales_tax_account = False
            for accc in self.Withholding_sales_tax_ids:
                for acccc in accc.invoice_repartition_line_ids.filtered(lambda x: x.account_id != False):
                    if acccc.account_id:
                        sales_tax_account = acccc.account_id.id
                sales_tax_amount = int(((self.amount_exclusive_sales_tax) * accc.amount / 100)+0.5)
                if liquidity_amount_currency > 0.0:
                    liquidity_amount_currency = liquidity_amount_currency - sales_tax_amount
                    liquidity_balance = liquidity_balance - sales_tax_amount

                if liquidity_amount_currency < 0.0:
                    liquidity_amount_currency = liquidity_amount_currency + sales_tax_amount
                    liquidity_balance = liquidity_balance + sales_tax_amount
                if liquidity_balance < 0.0:
                    sales_tax_amount = -sales_tax_amount
                if counterpart_balance > 0.0:
                    counterpart_balance += sales_tax_amount
                if counterpart_balance < 0.0:
                    counterpart_balance += sales_tax_amount

                line_vals_list.append(
                    {
                        "name": accc.name or liquidity_line_name or default_line_name,
                        "date_maturity": self.date,
                        "amount_currency": sales_tax_amount,
                        "currency_id": currency_id,
                        "debit": sales_tax_amount if sales_tax_amount > 0.0 else 0.0,
                        "credit": -sales_tax_amount if sales_tax_amount < 0.0 else 0.0,
                        "partner_id": self.partner_id.id,
                        "account_id": sales_tax_account,
                    }
                )

            if self.retention_money_payable:
                retention_money = self.retention_money_payable if liquidity_balance > 0.0 else -self.retention_money_payable
                liquidity_balance = liquidity_balance - retention_money if liquidity_balance > 0.0 else liquidity_balance - retention_money
                liquidity_amount_currency = liquidity_amount_currency - retention_money if liquidity_amount_currency > 0.0 else liquidity_amount_currency - retention_money
                retention_account_id = self.env['account.account'].search([('retention_money_payable','=',True)],limit=1)
                line_vals_list.append(
                    {
                        "name": retention_account_id.name or liquidity_line_name or default_line_name,
                        "date_maturity": self.date,
                        "amount_currency": retention_money,
                        "currency_id": currency_id,
                        "debit": retention_money if retention_money > 0.0 else 0.0,
                        "credit": -retention_money if retention_money < 0.0 else 0.0,
                        "partner_id": self.partner_id.id,
                        "account_id": retention_account_id.id,
                    }
                )
            if self.advance:
                advance = self.advance if liquidity_balance > 0.0 else -self.advance
                liquidity_balance = liquidity_balance - advance if liquidity_balance > 0.0 else liquidity_balance - advance
                liquidity_amount_currency = liquidity_amount_currency - advance if liquidity_amount_currency > 0.0 else liquidity_amount_currency - advance
                advance_account_id = self.env['account.account'].search([('advance','=',True)],limit=1)
                line_vals_list.append(

                    {
                        "name": advance_account_id.name or liquidity_line_name or default_line_name,
                        "date_maturity": self.date,
                        "amount_currency": advance,
                        "currency_id": currency_id,
                        "debit": advance if advance > 0.0 else 0.0,
                        "credit": -advance if advance < 0.0 else 0.0,
                        "partner_id": self.partner_id.id,
                        "account_id": advance_account_id.id,
                    }
                )

            line_vals_list.append(
                # Liquidity line.
                {
                    "name": liquidity_line_name or default_line_name,
                    "date_maturity": self.date,
                    "amount_currency": liquidity_amount_currency,
                    "currency_id": currency_id,
                    "debit": liquidity_balance if liquidity_balance > 0.0 else 0.0,
                    "credit": -liquidity_balance if liquidity_balance < 0.0 else 0.0,
                    "partner_id": self.partner_id.id,
                    "account_id": self.outstanding_account_id.id,
                })

        if not self.currency_id.is_zero(write_off_amount_currency):
            # Write-off line.
            line_vals_list.append(
                {
                    "name": write_off_line_val.get("name") or default_line_name,
                    "amount_currency": write_off_amount_currency,
                    "currency_id": currency_id,
                    "debit": write_off_balance if write_off_balance > 0.0 else 0.0,
                    "credit": -write_off_balance if write_off_balance < 0.0 else 0.0,
                    "partner_id": self.partner_id.id,
                    "account_id": write_off_line_val.get("account_id"),
                }
            )
        if write_off_line_vals and self.is_multi_deduction:
            for write_off_line_val in write_off_line_vals:
                balance2 = write_off_line_val.get("amount_currency")
                balance = self.currency_id._convert(
                    balance2,
                    self.company_id.currency_id,
                    self.company_id,
                    self.date,
                )
                if counterpart_balance > 0.0:
                    counterpart_balance += -balance
                if counterpart_balance < 0.0:
                    counterpart_balance += -balance

                line_vals_list.append(
                    {
                        "name": write_off_line_val.get("name") or default_line_name,
                        "amount_currency": balance2,
                        "currency_id": currency_id,
                        "debit": balance if balance > 0.0 else 0.0,
                        "credit": -balance if balance < 0.0 else 0.0,
                        "partner_id": self.partner_id.id,
                        "account_id": write_off_line_val.get("account_id"),
                    }
                )
        # _logger.info('counterpart_balance')
        # _logger.info(counterpart_balance)
        line_vals_list.append(
            {
                "name": self.payment_reference or default_line_name,
                "date_maturity": self.date,
                "amount_currency": counterpart_balance,
                "currency_id": currency_id,
                "debit": counterpart_balance if counterpart_balance > 0.0 else 0.0,
                "credit": -counterpart_balance if counterpart_balance < 0.0 else 0.0,
                "partner_id": self.partner_id.id,
                "account_id": self.destination_account_id.id,
            }
        )
        """Split amount to multi payment deduction
        Concept:
        * Process by payment difference 'Mark as fully paid' and keep value is paid
        * Process by each deduction and keep value is deduction
        * Combine all process and return list
        """

        # payment difference
        # if self.is_multi_deduction and write_off_line_vals:
        #     # update writeoff when edit value in payment
        #     writeoff_lines = self._seek_for_lines()[2]
        #     if not write_off_line_vals.get("analytic_distribution", False):
        #         write_off_line_vals[
        #             "analytic_distribution"
        #         ] = writeoff_lines.analytic_distribution
        #     # add analytic on line_vals_list
        #     check_keys = self._get_check_key_list()
        #     update_keys = self._get_update_key_list()
        #     self._update_vals_writeoff(
        #         write_off_line_vals, line_vals_list, check_keys, update_keys
        #     )
        for i in line_vals_list:
            _logger.info(i)
        return line_vals_list

    def _synchronize_from_moves(self, changed_fields):
        for record in self:

            if not record.is_wht_trx:
                # res = super(AccountPayment, self)._synchronize_from_moves(
                #     changed_fields
                # )
                pass
            else:
                """Update the account.payment regarding its related account.move.
                Also, check both models are still consistent.
                :param changed_fields: A set containing all modified fields on account.move.
                """
                if self._context.get("skip_account_move_synchronization"):
                    return

                for pay in self.with_context(skip_account_move_synchronization=True):
                    # After the migration to 14.0, the journal entry could be shared between the account.payment and the
                    # account.bank.statement.line. In that case, the synchronization will only be made with the statement line.
                    if pay.move_id.statement_line_id:
                        continue

                    move = pay.move_id
                    move_vals_to_write = {}
                    payment_vals_to_write = {}

                    if "journal_id" in changed_fields:
                        if pay.journal_id.type not in ("bank", "cash"):
                            raise UserError(
                                _(
                                    "A payment must always belongs to a bank or cash journal."
                                )
                            )

                    if "line_ids" in changed_fields:
                        all_lines = move.line_ids
                        (
                            liquidity_lines,
                            counterpart_lines,
                            writeoff_lines,
                        ) = pay._seek_for_lines()

                        if len(liquidity_lines) != 1 or len(counterpart_lines) != 1:
                            raise UserError(
                                _(
                                    "The journal entry %s reached an invalid state relative to its payment.\n"
                                    "To be consistent, the journal entry must always contains:\n"
                                    "- one journal item involving the outstanding payment/receipts account.\n"
                                    "- one journal item involving a receivable/payable account.\n"
                                    "- optional journal items, all sharing the same account.\n\n"
                                )
                                % move.display_name
                            )

                        # if writeoff_lines and len(writeoff_lines.account_id) != 1:
                        #
                        #     raise UserError(_(
                        #         "The journal entry %s reached an invalid state relative to its payment.\n"
                        #         "To be consistent, all the write-off journal items must share the same account."
                        #     ) % move.display_name)

                        if any(
                                line.currency_id != all_lines[0].currency_id
                                for line in all_lines
                        ):
                            raise UserError(
                                _(
                                    "The journal entry %s reached an invalid state relative to its payment.\n"
                                    "To be consistent, the journal items must share the same currency."
                                )
                                % move.display_name
                            )

                        if any(
                                line.partner_id != all_lines[0].partner_id
                                for line in all_lines
                        ):
                            raise UserError(
                                _(
                                    "The journal entry %s reached an invalid state relative to its payment.\n"
                                    "To be consistent, the journal items must share the same partner."
                                )
                                % move.display_name
                            )

                        # if counterpart_lines.account_id.user_type_id.type == 'receivable':
                        #     partner_type = 'customer'
                        # else:
                        #     partner_type = 'supplier'

                        liquidity_amount = liquidity_lines.amount_currency

                        move_vals_to_write.update(
                            {
                                "currency_id": liquidity_lines.currency_id.id,
                                "partner_id": liquidity_lines.partner_id.id,
                            }
                        )
                        payment_vals_to_write.update(
                            {
                                "amount": abs(liquidity_amount),
                                # 'partner_type': partner_type,
                                "currency_id": liquidity_lines.currency_id.id,
                                "destination_account_id": counterpart_lines.account_id.id,
                                "partner_id": liquidity_lines.partner_id.id,
                            }
                        )
                        if liquidity_amount > 0.0:
                            payment_vals_to_write.update({"payment_type": "inbound"})
                        elif liquidity_amount < 0.0:
                            payment_vals_to_write.update({"payment_type": "outbound"})

                    move.write(move._cleanup_write_orm_values(move, move_vals_to_write))
                    pay.write(
                        move._cleanup_write_orm_values(pay, payment_vals_to_write)
                    )


class AccountMove(models.Model):
    _inherit = "account.move"

    def button_draft_wht(self):
        print("button_draft_wht RUNNING........................")

        accountmoveline = self.env["account.move.line"]
        excluded_move_ids = []

        if self._context.get("suspense_moves_mode"):
            excluded_move_ids = (
                accountmoveline.search(
                    accountmoveline._get_suspense_moves_domain()
                    + [("move_id", "in", self.ids)]
                )
                .mapped("move_id")
                .ids
            )

        for move in self:
            if move in move.line_ids.mapped("full_reconcile_id.exchange_move_id"):
                raise UserError(
                    _("You cannot reset to draft an exchange difference journal entry.")
                )
            if move.tax_cash_basis_rec_id:
                raise UserError(
                    _("You cannot reset to draft a tax cash basis journal entry.")
                )
            if (
                    move.restrict_mode_hash_table
                    and move.state == "posted"
                    and move.id not in excluded_move_ids
            ):
                raise UserError(
                    _(
                        "You cannot modify a posted entry of this journal because it is in strict mode."
                    )
                )
            # We remove all the analytics entries for this journal
            move.mapped("line_ids.analytic_line_ids").unlink()

        self.mapped("line_ids").remove_move_reconcile()
        self.write({"state": "draft", "is_move_sent": False})
    @contextmanager
    def _check_balanced(self, container):
        ''' Assert the move is fully balanced debit = credit.
        An error is raised if it's not the case.
        '''
        with self._disable_recursion(container, 'check_move_validity', default=True, target=False) as disabled:
            yield
            if disabled:
                return

        unbalanced_moves = self._get_unbalanced_moves(container)
        _logger.info('123')
        _logger.info(unbalanced_moves)

        if unbalanced_moves:
            test_check = 0
            error_msg = _("An error has occurred.")
            for move_id, sum_debit, sum_credit in unbalanced_moves:
                move_line = self.env['account.move'].search([('id', '=', move_id)])
                _logger.info('move_linemove_linemove_linemove_line')
                _logger.info('move_linemove_linemove_linemove_line')
                _logger.info('move_linemove_linemove_linemove_line')
                _logger.info('move_linemove_linemove_linemove_line')
                _logger.info(move_line)
                _logger.info(move_line.line_ids)
                _logger.info(move_line.line_ids)
                _logger.info(move_line.line_ids)
                _logger.info(move_line.line_ids)
                debit = sum(move_line.mapped('line_ids').mapped('debit'))
                credit = sum(move_line.mapped('line_ids').mapped('credit'))
                test_check = 0
                if move_line.payment_id:
                    if debit != move_line.payment_id.amount or credit != move_line.payment_id.amount:
                        test_check = 1
                if move_line.statement_line_id:
                    if debit != move_line.statement_line_id.amount or credit != move_line.statement_line_id.amount:
                        test_check = 1

                if not test_check:
                    move = self.browse(move_id)
                    error_msg += _(
                        "\n\n"
                        "The move (%s) is not balanced.\n"
                        "The total of debits equals %s and the total of credits equals %s.\n"
                        "You might want to specify a default account on journal \"%s\" to automatically balance each move.",
                        move.display_name,
                        format_amount(self.env, sum_debit, move.company_id.currency_id),
                        format_amount(self.env, sum_credit, move.company_id.currency_id),
                        move.journal_id.name)
            if not test_check:
                raise UserError(error_msg)

    def _get_unbalanced_moves(self, container):
        moves = container['records'].filtered(lambda move: move.line_ids)
        if not moves:
            return
        _logger.info('_get_unbalanced_moves')
        _logger.info('_get_unbalanced_moves')
        _logger.info('_get_unbalanced_moves')
        _logger.info(moves)
        for rec in moves.line_ids:
            _logger.info(rec.debit)
            _logger.info(rec.credit)
        _logger.info(container['records'])
        # /!\ As this method is called in create / write, we can't make the assumption the computed stored fields
        # are already done. Then, this query MUST NOT depend on computed stored fields.
        # It happens as the ORM calls create() with the 'no_recompute' statement.
        d = self.env['account.move.line'].flush_model(['debit', 'credit', 'balance', 'currency_id', 'move_id'])
        _logger.info('d')
        _logger.info(d)
        _logger.info(moves.ids)
        self._cr.execute('''
            SELECT line.move_id,
                   ROUND(SUM(line.debit), currency.decimal_places) debit,
                   ROUND(SUM(line.credit), currency.decimal_places) credit
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN res_company company ON company.id = move.company_id
              JOIN res_currency currency ON currency.id = company.currency_id
             WHERE line.move_id IN %s
          GROUP BY line.move_id, currency.decimal_places
            HAVING ROUND(SUM(line.balance), currency.decimal_places) != 0
        ''', [tuple(moves.ids)])
        move_lines = self._cr.fetchall()
        _logger.info('move_lines')
        _logger.info(move_lines)
        return move_lines



class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    is_wht = fields.Boolean(string="is WHT")

    @api.model
    def _get_default_line_name(self, document, amount, currency, date, partner=None):
        """Helper to construct a default label to set on journal items.

        E.g. Vendor Reimbursement $ 1,555.00 - Azure Interior - 05/14/2020.

        :param document:    A string representing the type of the document.
        :param amount:      The document's amount.
        :param currency:    The document's currency.
        :param date:        The document's date.
        :param partner:     The optional partner.
        :return:            A string.
        """
        values = [
            "%s %s" % (document, formatLang(self.env, amount, currency_obj=currency))
        ]
        if partner:
            values.append(partner.display_name)
        values.append(format_date(self.env, fields.Date.to_string(date)))
        return " - ".join(values)

class AccountTax(models.Model):
    _inherit = "account.tax"

    withholding_tax = fields.Boolean(string="Income Tax Wth")
    sales_withholding_tax = fields.Boolean(string="Withholding Sales Tax")

class AccountAccount(models.Model):
    _inherit = "account.account"

    retention_money_payable = fields.Boolean(string="Retention Money Payable")
    advance = fields.Boolean(string="Advance")
