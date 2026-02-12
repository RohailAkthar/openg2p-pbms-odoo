from odoo import models, fields, api
from odoo.tools.safe_eval import safe_eval

import logging
import json
from dateutil.relativedelta import relativedelta
from odoo.addons.g2p_registry_type_addon.models import G2PRegistryType, G2PTargetModelMapping

_logger = logging.getLogger(__name__)

class G2PEntitlementRuleDefinition(models.Model):
    _name = "g2p.entitlement.rule.definition"
    _description = "G2P Program Entitlement Rule Definition"
    _rec_name = "mnemonic"

    mnemonic = fields.Char(string="Rule Mnemonic", required=True)
    description = fields.Char(string="Description")
    program_id = fields.Many2one("g2p.program.definition", string="G2P Program")
    benefit_code_id = fields.Many2one(
        'g2p.benefit.codes',
        string="Benefit Code",
        help="Select a benefit code defined in the selected program."
    )
    decimal_places = fields.Integer(
        string="Decimal Places",
        related="benefit_code_id.decimal_places",
        readonly=True,
        store=True,
    )
    measurement_unit = fields.Char(
        string="Measurement Unit",
        related="benefit_code_id.measurement_unit",
        readonly=True,
    )
    quantity = fields.Float(string="Quantity", required=True)
    multiplier = fields.Char(
        string="Multiplier",
        help="Select an integer field from the registry to use as a multiplier."
    )
    target_registry = fields.Selection(
        selection=G2PRegistryType.selection(), string="Target Registry", required=True
    )
    pbms_domain = fields.Char(string="Domain", required=True)
    target_model_name = fields.Char(compute="_compute_target_model_name")
    sql_query = fields.Char(string="SQL Query", compute="_get_query", store=True)

    @api.depends("target_registry")
    def _compute_target_model_name(self):
        for rec in self:
            rec.target_model_name = G2PTargetModelMapping.get_target_model_name(rec.target_registry)
    allowed_benefit_code_ids = fields.Many2many(
        'g2p.benefit.codes', related='program_id.benefit_code_ids', store=False
    )
    allowed_multipliers = fields.Char(compute='_compute_multiplier_options', store=False)

    @api.model
    def create(self, vals):
        multiplier_value = vals.get('multiplier')
        _logger.info("Multiplier in create: %s", multiplier_value)
        _logger.info("all VALS: %s", vals)

        # Add custom logic here
        return super().create(vals)
    
    @api.depends("target_registry")
    def _compute_multiplier_options(self):
        NUMERIC_FIELD_TYPES = {'integer', 'float', 'double', 'monetary', 'numeric', 'biginteger', 'smallinteger', 'decimal'}
        for rec in self:
            registry_type = rec.target_registry
            
            model_name = G2PTargetModelMapping.get_target_model_name(registry_type)
            if not model_name:
                rec.allowed_multipliers = "[]"
                continue

            model = self.env[model_name]
            numeric_fields = [
                (name, field.string or name)
                for name, field in model._fields.items()
                if field.type in NUMERIC_FIELD_TYPES and name != "id"
            ]
            rec.allowed_multipliers = json.dumps(numeric_fields)

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
                    "Error evaluating domain for entitlement rule %s: %s",
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
                id_field = "pensioner_zanid"
                alias = "link_registry_id"
            else:
                id_field = "link_registry_id"
                alias = "link_registry_id"
            # Extract the table alias from the FROM clause
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
                    "Error formatting query for entitlement rule %s: %s",
                    rec.mnemonic,
                    e,
                )
                rec.sql_query = "Error formatting query"
    
    def action_open_view(self):
        return {
            "type": "ir.actions.act_window",
            "name": "View Entitlement Rule Details",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "flags": {"mode": "readonly"},
            "views": [[False, "form"]],
            "context": dict(self._context, default_no_create=True, no_create=True, create=False),

        }

