from odoo import models, fields, api
from odoo.tools.safe_eval import safe_eval

import logging
from odoo.addons.g2p_registry_type_addon.models import G2PRegistryType, G2PTargetModelMapping

_logger = logging.getLogger(__name__)


class G2PEligibilityRuleDefinition(models.Model):
    _name = "g2p.eligibility.rule.definition"
    _description = "G2P Program Eligibility Rule Definition"
    _rec_name = "mnemonic"

    mnemonic = fields.Char(string="Rule Mnemonic", required=True)
    rule_number = fields.Integer(
        string="Rule Number",
        required=True,
        default=lambda self: self._get_default_rule_number(),
        store=True,
    )
    description = fields.Char(string="Description")
    program_id = fields.Many2one("g2p.program.definition", string="G2P Program")
    target_registry = fields.Selection(
        selection=G2PRegistryType.selection(), string="Target Registry", required=True
    )
    set_operator = fields.Selection(
        selection=[
            ('NONE', 'NONE'),
            ('INTERSECT', 'INTERSECT'),
            ('EXCEPT', 'EXCEPT'),
            ('UNION', 'UNION'),
        ],
        string="Set Operator",
        default='NONE',
        store=True,
        required=True
    )
    pbms_domain = fields.Char(string="Domain", required=True)
    sql_query = fields.Char(string="SQL Query", compute="_get_query", store=True)

    _sql_constraints = [
        ('rule_number_program_id_unique', 'unique(rule_number, program_id)', 'Rule Number must be unique per Program.')
    ]


    @api.model
    def _get_default_rule_number(self):
        program_id = self.env.context.get('default_program_id')
        last = self.search([('program_id', '=', program_id)], order='rule_number desc', limit=1)
        return last.rule_number + 1 if last else 1

    @api.depends("pbms_domain", "target_registry")
    def _get_query(self):
        for rec in self:
            try:
                # Convert the domain string into a valid Python list of tuples.
                domain_value = safe_eval(rec.pbms_domain or "[]")
            except Exception as e:
                _logger.error(
                    "Error evaluating domain for eligibility rule %s: %s",
                    rec.mnemonic,
                    e,
                )
                rec.sql_query = "Invalid domain"
                continue

            target_model_name = G2PTargetModelMapping.get_target_model_name(rec.target_registry)
            if not target_model_name:
                _logger.error(
                    "Unknown target_registry '%s' for rule %s",
                    rec.target_registry,
                    rec.mnemonic,
                )
                rec.sql_query = "Unknown target registry type"
                continue

            # Get the target model's recordset.
            target_model = self.env[target_model_name]

            try:
                # Calculate the where clause for the target model based on the evaluated domain.
                query = target_model._where_calc(domain_value)
            except Exception as e:
                _logger.error(
                    "Error calculating where clause for rule %s: %s", rec.mnemonic, e
                )
                rec.sql_query = "Error calculating query"
                continue

            try:
                from_clause, where_clause, where_clause_params = query.get_sql()
            except Exception as e:
                _logger.error(
                    "Error generating SQL from query for rule %s: %s", rec.mnemonic, e
                )
                rec.sql_query = "Error generating SQL"
                continue

            where_str = (" WHERE %s" % where_clause) if where_clause else ""
            # Use the target model's table name in the SQL query.
            query_str = (
                'SELECT "%s".link_registry_id FROM ' % target_model._table + from_clause + where_str
            )

            try:
                import re
                parts = query_str.split("%s")
                if len(parts) - 1 == len(where_clause_params):
                    new_parts = [parts[0]]
                    formatted_params = []
                    for i, param in enumerate(where_clause_params):
                        prev_part = new_parts[-1]
                        if isinstance(param, str):
                            match = re.search(r'=\s*$', prev_part)
                            if match:
                                prev_part = prev_part[:match.start()] + ' ILIKE '
                                new_parts[-1] = prev_part
                            formatted_params.append("'" + str(param).replace("'", "''") + "'")
                        else:
                            formatted_params.append(str(param))
                        new_parts.append(parts[i + 1])
                    rec.sql_query = "%s".join(new_parts) % tuple(formatted_params)
                else:
                    formatted_params = list(map(lambda x: "'" + str(x).replace("'", "''") + "'", where_clause_params))
                    rec.sql_query = query_str % tuple(formatted_params)
                _logger.info("Query for rule %s: %s", rec.mnemonic, rec.sql_query)
            except Exception as e:
                _logger.error(
                    "Error formatting query for eligibility rule %s: %s",
                    rec.mnemonic,
                    e,
                )
                rec.sql_query = "Error formatting query"

    def action_open_view(self):
        return {
            "type": "ir.actions.act_window",
            "name": "View Eligibility Rule Details",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "flags": {"mode": "readonly"},
            "views": [[False, "form"]],
            "context": dict(self._context, default_no_create=True, no_create=True, create=False),
        }
