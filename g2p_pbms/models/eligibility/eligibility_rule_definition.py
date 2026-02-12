from odoo import models, fields, api
from odoo.tools.safe_eval import safe_eval

import logging
from dateutil.relativedelta import relativedelta
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
    target_model_name = fields.Char(compute="_compute_target_model_name")
    sql_query = fields.Char(string="SQL Query", compute="_get_query", store=True)

    @api.depends("target_registry")
    def _compute_target_model_name(self):
        for rec in self:
            rec.target_model_name = G2PTargetModelMapping.get_target_model_name(rec.target_registry)

    _sql_constraints = [
        ('rule_number_program_id_unique', 'unique(rule_number, program_id)', 'Rule Number must be unique per Program.')
    ]


    @api.model
    def _get_default_rule_number(self):
        program_id = self.env.context.get('default_program_id')
        last = self.search([('program_id', '=', program_id)], order='rule_number desc', limit=1)
        return last.rule_number + 1 if last else 1

    def _rewrite_age_domain(self, term):
        if not (isinstance(term, (list, tuple)) and len(term) == 3 and term[0] == 'age'):
            return term

        field, operator, value = term
        today = fields.Date.context_today(self)
        try:
            years = int(value)
        except (ValueError, TypeError):
            return term

        start = today - relativedelta(years=years)
        end = today - relativedelta(years=years + 1)

        if operator == '>=':
            return ('birthdate', '<=', start)
        if operator == '>':
            return ('birthdate', '<=', end)
        if operator == '<=':
            return ('birthdate', '>', end)
        if operator == '<':
            return ('birthdate', '>', start)
        if operator == '=':
            return [
                '&',
                ('birthdate', '<=', start),
                ('birthdate', '>', end),
            ]
        if operator == '!=':
            return [
                '|',
                ('birthdate', '>', start),
                ('birthdate', '<=', end),
            ]
        return term

    def _preprocess_domain(self, domain):
        if not isinstance(domain, list):
            return domain

        new_domain = []
        for term in domain:
            if isinstance(term, (list, tuple)):
                rewritten = self._rewrite_age_domain(term)
                if isinstance(rewritten, list):
                    new_domain.extend(rewritten)
                else:
                    new_domain.append(rewritten)
            else:
                new_domain.append(term)
        return new_domain

    @api.depends("pbms_domain", "target_registry")
    def _get_query(self):
        for rec in self:
            try:
                # Convert the domain string into a valid Python list of tuples.
                domain_value = safe_eval(rec.pbms_domain or "[]")
                domain_value = rec._preprocess_domain(domain_value)
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
            if target_model._name == "res.partner":
                id_field = "benf_zan_id"
                alias = "link_registry_id"
            else:
                id_field = "link_registry_id"
                alias = "link_registry_id"
            # Extract the table alias from the FROM clause.
            tokens = from_clause.strip().split()

            if len(tokens) >= 3 and tokens[1].lower() == "as":
                table_alias = tokens[2]
            else:
                table_alias = tokens[0]

            query_str = (
                "SELECT %s.%s::TEXT AS %s FROM "
                % (table_alias, id_field, alias)
                + from_clause
                + where_str
            )

            # Format the parameters as strings.
            formatted_params = list(
                map(lambda x: "'" + str(x) + "'", where_clause_params)
            )

            try:
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
