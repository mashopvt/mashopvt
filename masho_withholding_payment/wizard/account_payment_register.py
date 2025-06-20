# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError, ValidationError

import logging
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)
class AccountPaymentRegister(models.TransientModel):
    _name = "account.payment.register"
    _inherit = ["account.payment.register", "analytic.mixin"]

    # def _get_default_amount_exclusive_sales_tax(self):
    #     active_ids = self._context.get('active_id', [])
    #     invoices = self.env['account.move'].browse(active_ids)
    #     amount = sum(invoices.mapped('invoice_line_ids').mapped('price_subtotal'))
    #     self.amount_exclusive_sales_tax = amount
    #     self.lc_note = invoices.lc_note
    #     self.amount_inclusive_sales_tax = sum(invoices.mapped('invoice_line_ids').mapped('price_total'))
    #     taxes = []
    #     if invoices.mapped('invoice_line_ids'):
    #
    #         for i in invoices.mapped('invoice_line_ids'):
    #             for j in i.tax_ids:
    #                 taxes.append(j.amount)
    #         self.tax_percent = sum(taxes) / len(invoices.mapped('invoice_line_ids'))
    #     else:
    #         self.tax_percent = 0.0


    registered_partner = fields.Boolean(string="Registered Partner")
    #
    # @api.depends('partner_id')
    # def compute_registered_partner(self):
    #     for rec in self:
    #         if rec.partner_id:
    #             rec.registered_partner = rec.partner_id.registered_partner
    #         else:
    #             rec.registered_partner = False

    payment_difference_handling = fields.Selection(
        selection_add=[
            ("reconcile_multi_deduct", "Mark invoice as fully paid (multi deduct)")
        ],
        ondelete={"reconcile_multi_deduct": "cascade"},
    )
    deduct_residual = fields.Monetary(
        string="Remainings", compute="_compute_deduct_residual"
    )
    deduction_ids = fields.One2many(
        comodel_name="account.payment.deduction",
        inverse_name="payment_id",
        string="Deductions",
        copy=False,
        help="Sum of deduction amount(s) must equal to the payment difference",
    )
    deduct_analytic_distribution = fields.Json()

    wht_tax_line_ids = fields.One2many("withholding.tax.line", 'payment_reg_id', string="Payment Register Line")
    # withholding_tax_id = fields.Many2many('account.tax', string="Income Tax Wth",store=True, readonly=False)
    withholding_tax_ids = fields.Many2many(
        "account.tax",
        "withholding_tax_ids_rel222",
        string="Income Tax Wths",
        compute="tax_type_withholding",
        store=True,
        readonly=False,
    )

    withholding_tax_id = fields.Many2many(
        "account.tax","withholding_tax_id_rel22",
        string="Income Tax Wth",
        domain="[('id','in',withholding_tax_ids)]",
        store=True,

    )

    sales_tax_ids = fields.Many2many(
        "account.tax",
        'sales_tax_rel',
        string="Sales Tax",
        store=True,
    )
    Withholding_sales_tax_ids = fields.Many2many(
        "account.tax",
        'Withholding_sales_id_rel',
        string="Sales Tax",
        store=True,
        domain="[('sales_withholding_tax','=',True)]"
    )
    retention_money_payable = fields.Float(string="Retention Money Payable")
    advance = fields.Float(string="Advance")
    amount_payable = fields.Float(string="Amount Payable",compute="compute_amount_payable")
    lc_note = fields.Char(string="LC")
    amount_exclusive_sales_tax = fields.Float(string="Exclusive Sales Tax Amount")
    amount_inclusive_sales_tax = fields.Float(string="Inclusive Sales Tax Amount")
    sales_tax_amount = fields.Float(string="Sales Tax Amount", store=True,compute="compute_amount_inclusive_sales_tax")
    sales_tax_amount_withholding = fields.Float(string="Sales Tax Amount WHT", store=True,compute="compute_amount_inclusive_sales_tax")
    amount_withholding = fields.Float(string="Income Tax Amount WHT", store=True)
    tax_percent = fields.Float(string="Tax Percent", store=True)
    sale_tax_ids = fields.Many2many(
        "account.tax",
        'sale_tax_idssss_rel',
        string="Sales Tax",
        store=True,
    )

    @api.onchange('amount_inclusive_sales_tax','sales_tax_amount_withholding','amount_withholding','advance','retention_money_payable')
    def compute_amount_payable(self):
        if self.amount_inclusive_sales_tax:
            self.amount_payable = self.amount_inclusive_sales_tax - self.retention_money_payable - self.sales_tax_amount_withholding - self.amount_withholding - self.advance
        else:
            self.amount_payable = self.amount_exclusive_sales_tax - self.retention_money_payable - self.sales_tax_amount_withholding - self.amount_withholding - self.advance

    @api.onchange('journal_id')
    def _get_default_amount_exclusive_sales_tax(self):
        active_ids = self._context.get('active_id', [])
        invoices = self.env['account.move'].browse(active_ids)
        amount = sum(invoices.mapped('invoice_line_ids').mapped('price_subtotal'))
        taxes = tax_ids = []

        if invoices.mapped('invoice_line_ids'):
            tax_ids = invoices.mapped('invoice_line_ids').mapped('tax_ids')

            for j in tax_ids:
                taxes.append(j.amount)
            if taxes and tax_ids:
                self.tax_percent = sum(taxes) / len(tax_ids.ids)
            else:
                self.tax_percent = 0.0

            self.sale_tax_ids = tax_ids.ids
        else:
            self.tax_percent = 0.0

        if invoices.amount_residual != sum(invoices.mapped('invoice_line_ids').mapped('price_total')):
            percent_payable = (invoices.amount_residual * 100) / sum(invoices.mapped('invoice_line_ids').mapped('price_total'))
            amount = (amount * (percent_payable)) / 100
        self.amount_exclusive_sales_tax = amount
        self.lc_note = invoices.lc_note
        self.amount_inclusive_sales_tax = sum(invoices.mapped('invoice_line_ids').mapped('price_total'))
        # if invoices.mapped('invoice_line_ids'):
        #
        #     for i in invoices.mapped('invoice_line_ids'):
        #         for j in i.tax_ids:
        #             taxes.append(j.amount)
        #     self.tax_percent = sum(taxes) / len(invoices.mapped('invoice_line_ids'))
        # else:
        #     self.tax_percent = 0.0


    # @api.onchange('amount_exclusive_sales_tax','amount_inclusive_sales_tax')
    # def onchange_amount_exclusive(self):
    #     self.amount_inclusive_sales_tax = self.sales_tax_amount + self.amount_exclusive_sales_tax

    def _update_vals_deduction(self, moves):
        move_lines = moves.mapped("line_ids")
        analytic = {}
        [
            analytic.update(item)
            for item in move_lines.mapped("analytic_distribution")
            if item
        ]
        self.analytic_distribution = analytic

    def _update_vals_multi_deduction(self, moves):
        move_lines = moves.mapped("line_ids")
        analytic = {}
        [
            analytic.update(item)
            for item in move_lines.mapped("analytic_distribution")
            if item
        ]
        self.deduct_analytic_distribution = analytic

    @api.onchange("payment_difference", "payment_difference_handling")
    def _onchange_default_deduction(self):
        active_ids = self.env.context.get("active_id", [])
        print(active_ids)
        moves = self.env["account.move"].browse(active_ids)
        if self.payment_difference_handling == "reconcile":
            self._update_vals_deduction(moves)
        if self.payment_difference_handling == "reconcile_multi_deduct":
            self._update_vals_multi_deduction(moves)

    @api.constrains("deduction_ids", "payment_difference_handling")
    def _check_deduction_amount(self):
        prec_digits = self.env.user.company_id.currency_id.decimal_places
        for rec in self:
            if rec.payment_difference_handling == "reconcile_multi_deduct":
                if (
                    float_compare(
                        rec.payment_difference,
                        sum(rec.deduction_ids.mapped("amount")),
                        precision_digits=prec_digits,
                    )
                    != 0
                ):
                    raise UserError(
                        _("The total deduction should be %s") % rec.payment_difference
                    )

    @api.depends("payment_difference", "deduction_ids")
    def _compute_deduct_residual(self):
        for rec in self:
            rec.deduct_residual = rec.payment_difference - sum(
                rec.deduction_ids.mapped("amount")
            )

    def _prepare_deduct_move_line(self, deduct):
        conversion_rate = self.env["res.currency"]._get_conversion_rate(
            self.currency_id,
            self.company_id.currency_id,
            self.company_id,
            self.payment_date,
        )
        write_off_amount_currency = (
            deduct.amount if self.payment_type == "inbound" else -deduct.amount
        )
        write_off_balance = self.company_id.currency_id.round(
            write_off_amount_currency * conversion_rate
        )
        return {
            "name": deduct.name,
            "account_id": deduct.account_id.id,
            "partner_id": self.partner_id.id,
            "currency_id": self.currency_id.id,
            "amount_currency": write_off_amount_currency,
            "balance": write_off_balance,
            "analytic_distribution": deduct.analytic_distribution,
        }

    # @api.depends('sales_tax_ids')
    # def compute_sales_tax(self):
    #     for rec in self:
    #         if rec.sales_tax_ids:
    #             rec.Withholding_sales_tax_ids = rec.sales_tax_ids.ids
    #         else:
    #             rec.Withholding_sales_tax_ids = False

    @api.depends('amount_inclusive_sales_tax','amount_exclusive_sales_tax','Withholding_sales_tax_ids','retention_money_payable','advance')
    def compute_amount_inclusive_sales_tax(self):
        for rec in self:
            rec.sales_tax_amount = (rec.tax_percent * rec.amount_exclusive_sales_tax / 100)
            rec.amount_inclusive_sales_tax = (rec.amount_exclusive_sales_tax) + rec.sales_tax_amount
            if rec.Withholding_sales_tax_ids:
                sales_tax_amount_withholding = 0.0
                amount_inclusive_sales_tax = 0.0
                for sales_tax_id in rec.Withholding_sales_tax_ids:
                    sales_tax_amount_withholding += (rec.amount_exclusive_sales_tax - rec.retention_money_payable - rec.advance) * sales_tax_id.amount / 100
                rec.sales_tax_amount_withholding = sales_tax_amount_withholding
            else:
                rec.sales_tax_amount_withholding = 0.0
            # rec.amount = rec.amount_inclusive_sales_tax

    @api.onchange('amount_inclusive_sales_tax')
    def onchange_amount_inclusive_sales_tax_amount(self):
        self.amount = self.amount_inclusive_sales_tax
    # @api.depends('amount_inclusive_sales_tax','amount_exclusive_sales_tax','Withholding_sales_tax_ids','retention_money_payable')
    # def compute_amount_inclusive_sales_tax(self):
    #     for rec in self:
    #         if rec.Withholding_sales_tax_ids:
    #             sales_tax_amount_withholding = 0.0
    #             for sales_tax_id in rec.Withholding_sales_tax_ids:
    #                 sales_tax_amount_withholding += (rec.amount_exclusive_sales_tax - rec.retention_money_payable) * (sales_tax_id.amount / 100)
    #             rec.sales_tax_amount_withholding = sales_tax_amount_withholding
    #         else:
    #             rec.sales_tax_amount_withholding = 0.0
    #         if not rec.amount_inclusive_sales_tax:
    #             rec.amount_inclusive_sales_tax = rec.amount_exclusive_sales_tax - rec.retention_money_payable
    #         rec.sales_tax_amount = rec.amount_inclusive_sales_tax - rec.amount_exclusive_sales_tax - rec.retention_money_payable


    @api.depends('partner_id', 'payment_type')
    def tax_type_withholding(self):
        taxes = False
        if self.payment_type == "outbound":
            if self.partner_id:
                if self.partner_id.withholding_tax_ids:
                    taxes = self.env["account.tax"].search([("type_tax_use", "=", "purchase"),("id", "in", self.partner_id.withholding_tax_ids.ids),("sales_withholding_tax", "=", False)])
                else:
                    taxes = self.env["account.tax"].search([("type_tax_use", "=", "purchase"),("withholding_tax", "=", True),("sales_withholding_tax", "=", False)])
            else:
                taxes = self.env["account.tax"].search([("type_tax_use", "=", "purchase"), ("withholding_tax", "=", True),("sales_withholding_tax", "=", False)])
            if taxes:
                self.withholding_tax_ids = taxes.ids
            else:
                self.withholding_tax_ids = False

            #     return {"domain": {"withholding_tax_id": [("id", "in", taxes.ids)]}}
            # else:
            #     return {"domain": {"withholding_tax_id": [("id", "!=", False)]}}
        elif self.payment_type == "inbound":
            if self.partner_id:
                if self.partner_id.withholding_tax_ids:
                    taxes = self.env["account.tax"].search([("type_tax_use", "=", "sale"),("id", "in", self.partner_id.withholding_tax_ids.ids),("sales_withholding_tax", "=", False)])
                else:
                    taxes = self.env["account.tax"].search([("type_tax_use", "=", "sale"), ("withholding_tax", "=", True),("sales_withholding_tax", "=", False)])
            else:
                taxes = self.env["account.tax"].search([("type_tax_use", "=", "sale"), ("withholding_tax", "=", True),("sales_withholding_tax", "=", False)])
            if taxes:
                self.withholding_tax_ids = taxes.ids
            else:
                self.withholding_tax_ids = False
        # if taxes:
        #     return {"domain": {"withholding_tax_id": [("id", "in", taxes.ids)]}}
        # else:
        #     return {"domain": {"withholding_tax_id": [("id", "!=", False)]}}

    # @api.onchange('withholding_tax_id','amount_exclusive_sales_tax','amount_inclusive_sales_tax', 'amount','retention_money_payable','advance')
    # def _onchange_wth_tax_amount(self):
    #     if self.withholding_tax_id:
    #         x = []
    #         _logger.info('kkkkkkkkkkkkkkkkkkkkkkkk')
    #         for rec in self.withholding_tax_id:
    #             if self.sales_tax_amount:
    #                 _logger.info('1111111111111111111111111111111111')
    #                 x.append(
    #                     (self.amount_inclusive_sales_tax - self.retention_money_payable - self.advance) * rec.amount / 100
    #                 )
    #                 self.amount_withholding = sum(x)
    #             elif self.amount_exclusive_sales_tax:
    #                 _logger.info('2222222222222222222222222222222222222')
    #                 x.append(
    #                     ((self.amount_exclusive_sales_tax - self.retention_money_payable - self.advance) * rec.amount / 100)
    #                 )
    #                 self.amount_withholding = sum(x)
    #             else:
    #                 _logger.info('3333333333333333333333333333')
    #                 self.amount_withholding = 0
    #     else:
    #         self.amount_withholding = 0

    @api.onchange('withholding_tax_id', 'amount','retention_money_payable','advance')
    def _onchange_wth_tax_amount(self):
        if self.withholding_tax_id:
            x = []
            for rec in self.withholding_tax_id:
                if self.sales_tax_amount:
                    self.amount_withholding = ((self.amount_inclusive_sales_tax - self.retention_money_payable - self.advance) * (rec.amount / 100))

                elif self.amount_exclusive_sales_tax:
                    self.amount_withholding = ((self.amount_exclusive_sales_tax - self.retention_money_payable - self.advance) * (rec.amount / 100))
                else:
                    self.amount_withholding = 0
        else:
            self.amount_withholding = 0
    # @api.model
    # def _get_wizard_values_from_batch(self, batch_result):
    # def _get_wizard_values_from_batch(self, batch_result):
    #     print("_get_wizard_values_from_batch OVERRIDE RUNNING..................")
    #     res = super(AccountPaymentRegister, self)._get_wizard_values_from_batch(batch_result)
    #
    #     temp = []
    #     if batch_result:
    #         move_id = False
    #         for data in batch_result['lines'][:1]:
    #             move_line_id = self.env['account.move.line'].browse(data.id)
    #             move_id = move_line_id.move_id
    #         if move_id:
    #             # payment_id = self.env['account.payment'].search([('ref','=',move_id.name)])
    #             # if payment_id and not payment_id.is_wht_trx:
    #             # group invoice line by withholding_tax
    #             wht_tax_ids = []
    #             for rec in move_id.invoice_line_ids:
    #                 if rec.withholding_tax_id and rec.withholding_tax_id.id not in wht_tax_ids:
    #                     wht_tax_ids.append(rec.withholding_tax_id.id)
    #             # sum witholding_subtotal by tax
    #             for tax in wht_tax_ids:
    #                 subtotal_amount = 0
    #                 for move in move_id.invoice_line_ids:
    #                     if move.withholding_tax_id and move.withholding_tax_id.id == tax:
    #                         subtotal_amount += move.withholding_subtotal
    #
    #                 tax_id = self.env['account.tax'].browse(tax)
    #                 vals = {
    #                     "tax_id": tax,
    #                     "name": tax_id.name,
    #                     "amount_withholding": subtotal_amount,
    #                 }
    #                 temp.append((0, 0, vals))
    #
    #             if not move_id.wht_executed:
    #                 self.write({"wht_tax_line_ids": False})
    #                 self.write({"wht_tax_line_ids": temp})
    #
    #     return res


    # def _create_payments(self):
    #     print("_create_payments OVERRIDE RUNNING........................")
    #     res = super(AccountPaymentRegister, self)._create_payments()
    #     print('sssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssseeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeerrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr')
    #     if self.withholding_tax_id:
    #         print('tax id', self.withholding_tax_id.name)
    #         if self.amount_withholding:
    #             print(self.amount_withholding)
    #             inv_id = self.env['account.payment'].search(
    #                 [('partner_id', '=', self.partner_id.id), ('payment_type', '=', 'outbound'),
    #                  ('ref', '=', self.communication), ('date', '=', self.payment_date)], limit=1)
    #             if inv_id:
    #                 print('inv_id', inv_id)
    #                 inv_id.write({
    #                     'withholding_tax_id': self.withholding_tax_id,
    #                     'amount_withholding': self.amount_withholding,
    #                 })
    #
    #
    #
    #     return res
    def prepare_journal_line(self):

        return {
            'name': 'Discount',
            'quantity': 1,
            'debit': self.amount_withholding,
            'account_id': self.withholding_tax_id.invoice_repartition_line_ids.account_id.id,
            'sequence': 500000,
        }
    # def _post_payments(self, to_process, edit_mode=False):
    #     """ Post the newly created payments.
    #
    #     :param to_process:  A list of python dictionary, one for each payment to create, containing:
    #                         * create_vals:  The values used for the 'create' method.
    #                         * to_reconcile: The journal items to perform the reconciliation.
    #                         * batch:        A python dict containing everything you want about the source journal items
    #                                         to which a payment will be created (see '_get_batches').
    #     :param edit_mode:   Is the wizard in edition mode.
    #     """
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     _logger.info('Not Posted')
    #     payments = self.env['account.payment']
    #     for vals in to_process:
    #         payments |= vals['payment']
    #     payments.action_post()
    #     payments.action_draft()

    def _create_payment_vals_from_wizard(self, batch_result):
        payment_vals = super()._create_payment_vals_from_wizard(batch_result)
        # payment difference
        payment_vals.update(
            {
                "date": self.payment_date,
                "amount": self.amount,
                "Withholding_sales_tax_ids": [(6,0,self.Withholding_sales_tax_ids.ids)],
                "retention_money_payable": self.retention_money_payable,
                "advance": self.advance,
                "tax_percent": self.tax_percent,
                "sale_tax_ids": self.sale_tax_ids.ids,
                "lc_note": self.lc_note,
                "amount_exclusive_sales_tax": self.amount_exclusive_sales_tax,
                "amount_inclusive_sales_tax": self.amount_inclusive_sales_tax,
                "withholding_tax_id": [(6,0,self.withholding_tax_id.ids)],
                "payment_type": self.payment_type,
                "partner_type": self.partner_type,
                "memo": self.communication,
                "journal_id": self.journal_id.id,
                "currency_id": self.currency_id.id,
                "partner_id": self.partner_id.id,
                "partner_bank_id": self.partner_bank_id.id,
                "payment_method_line_id": self.payment_method_line_id.id,
                "destination_account_id": self.line_ids[0].account_id.id,
            }
        )
        _logger.info(payment_vals)
        _logger.info(self.payment_difference_handling)
        _logger.info(self.payment_difference_handling)
        _logger.info(self.payment_difference_handling)
        _logger.info(self.payment_difference_handling)
        _logger.info(self.payment_difference_handling)
        if (
                not self.currency_id.is_zero(self.payment_difference)
                and self.payment_difference_handling == "reconcile"
        ):
            payment_vals["write_off_line_vals"] = {
                "name": self.writeoff_label,
                "amount": self.payment_difference,
                "account_id": self.writeoff_account_id.id,
            }
        if (not self.currency_id.is_zero(self.payment_difference) and self.payment_difference_handling == "reconcile"):
            payment_vals["write_off_line_vals"][
                "analytic_distribution"
            ] = self.analytic_distribution
        # multi deduction
        elif (self.payment_difference and self.payment_difference_handling == "reconcile_multi_deduct"):
            payment_vals["write_off_line_vals"] = [self._prepare_deduct_move_line(deduct) for deduct in self.deduction_ids.filtered(lambda l: not l.is_open)]
            payment_vals["is_multi_deduction"] = True

        return payment_vals



class WithholdingTaxLine(models.TransientModel):
    _name = 'withholding.tax.line'
    _description = "withholding.tax.line"

    payment_reg_id = fields.Many2one('account.payment.register', string="Account Payment Register")
    tax_id = fields.Many2one('account.tax', string="Taxes")
    name = fields.Char(string="Description")
    amount_withholding = fields.Float(string="Amount WHT")


class AccountMove(models.Model):
    _inherit = 'account.move'

    lc_note = fields.Char(string="LC")
    lc_note_2 = fields.Char(string="LC",compute="compute_lc_note")

    @api.depends('name','journal_id','line_ids','invoice_line_ids','date','payment_ids')
    def compute_lc_note(self):
        for rec in self:
            if rec.payment_ids:
                rec.lc_note_2 = rec.payment_ids[0].lc_note
            else:
                rec.lc_note_2 = False
