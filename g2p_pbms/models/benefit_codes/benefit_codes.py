from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class G2PBenefitCodes(models.Model):
    _name = "g2p.benefit.codes"
    _description = "Benefit Codes"
    _table = "g2p_benefit_codes"
    _rec_name = "benefit_mnemonic"

    benefit_mnemonic = fields.Char(string="Benefit Mnemonic", required=True)
    benefit_type = fields.Selection(
        [   
            ("CASH_DIGITAL", "Cash (Digital)"),
            ("CASH_PHYSICAL", "Cash (Physical)"),
            ("COMMODITY", "Commodity"),
            ("SERVICE", "Service"),
            ("COMBINATION", "Combination"),
        ],
        string="Benefit Type",
        required=True,
    )
    benefit_description = fields.Text(string="Benefit Description")
    decimal_places = fields.Integer(
        string="Decimal Places",
        help="Number of decimal places to use for this benefit code's value. Maximum allowed value is 4. This field can only be set during creation, updation can not and should not be done.",
        default=0,
        store=True
    )

    measurement_unit = fields.Char(string="Measurement Unit")



    _sql_constraints = [
        (
            "unique_benefit_mnemonic",
            "unique(benefit_mnemonic)",
            "The benefit mnemonic must be unique!",
        )
    ]

    @api.model
    def create(self, vals):
        # Clamp decimal_places between 0 and 4
        if 'decimal_places' in vals:
            try:
                dp = int(vals['decimal_places'])
            except Exception:
                dp = 0
            if dp < 0:
                dp = 0
            elif dp > 4:
                dp = 4
            vals['decimal_places'] = dp
        return super(G2PBenefitCodes, self).create(vals)

    def write(self, vals):
        if 'decimal_places' in vals:
            vals.pop('decimal_places')
        return super(G2PBenefitCodes, self).write(vals)

    def action_open_view(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "View Benefit Code Details",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "flags": {"mode": "readonly"},
        }

    def action_open_edit(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'g2p.benefit.codes',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
            'context':{'create': False, 'benefit_code_form_edit':True},
        }

