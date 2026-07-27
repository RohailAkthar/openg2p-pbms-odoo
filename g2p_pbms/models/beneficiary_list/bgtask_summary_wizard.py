from datetime import datetime
import json
import logging
import requests
from odoo.exceptions import UserError, AccessError
from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval

from odoo.addons.g2p_registry_type_addon.models import (
    G2PTargetModelMapping,
    G2PRegistryType,
)

_logger = logging.getLogger(__name__)

class G2PBGTaskSummaryWizard(models.TransientModel):
    _name = 'g2p.bgtask.summary.wizard'
    _description = 'Background Task Summary Wizard'
    _rec_name = 'mnemonic'

    target_registry = fields.Selection(
        selection=lambda self: G2PRegistryType.selection(),
        string="Registry Type",
        required=True,
    )
    mnemonic= fields.Char(string='Mnemonic')
    brief = fields.Text(string='Brief')
    program_id = fields.Many2one('g2p.program.definition', string='Program')
    beneficiary_list_id = fields.Integer(string='Beneficiary List ID')
    beneficiary_list_uuid = fields.Char(string='Beneficiary List ID')
    enrollment_cycle_id = fields.Integer(string='Enrollment Cycle')
    disbursement_cycle_id = fields.Integer(string='Disbursement Cycle')
    beneficiary_search = fields.Char(string='Search Beneficiary')
    list_stage = fields.Char(string='List Stage', default="enrollment")
    list_workflow_status = fields.Char(string='List Workflow Status', default="initiated")

    verification_ids = fields.One2many(
        "storage.file",
        string="Community Verification",
        compute="_compute_verification_ids",
        default=False
    )

    # Enrollment Cycle Info
    enrollment_start_date = fields.Date(string='Enrollment Start Date')
    enrollment_end_date = fields.Date(string='Enrollment End Date')
    approved_for_enrollment = fields.Boolean(string='Approved for Enrollment', default=False)

    # Disbursement Cycle Info
    disbursement_cycle_mnemonic = fields.Char(string='Disbursement Cycle')
    approved_for_disbursement = fields.Boolean(string='Approved for Disbursement', default=False)

    # Store all summary lines from the API response
    summary_line_ids = fields.One2many(
        'g2p.api.summary.line', 'wizard_id', string='Summary Details',
        compute='_compute_summary_lines', store=True
    )
    summary_general_line_ids = fields.One2many(
        'g2p.api.summary.line', 'wizard_id', string='General Info',
        compute='_compute_general'
    )
    summary_eligibility_line_ids = fields.One2many(
        'g2p.api.summary.line', 'wizard_id', string='Registry Info',
        compute='_compute_eligibility'
    )
    summary_entitlement_line_ids = fields.One2many(
        'g2p.api.summary.line', 'wizard_id', string='Registry Info',
        compute='_compute_entitlement'
    )
    general_title = fields.Char(compute='_compute_general_title', string="Group Title")
    eligibility_group_title = fields.Char(compute='_compute_eligibility_group_title', string="Group Title")
    entitlement_group_title = fields.Char(compute='_compute_entitlement_group_title', string="Group Title")

    dummy_beneficiaries_field = fields.Text(string="Beneficiaries", compute="_compute_dummy")

    sql_query = fields.Char(string="Query", store=True)
    order_by_condition = fields.Char(string="Order By", default="name")

    # Fields for Disbursement Envelope and Disbursement Batch Lines
    disbursement_envelope_line_ids = fields.One2many(
        'g2p.api.disbursement.envelope.line', 'wizard_id', string='Disbursement Envelopes',
        compute='_compute_disbursement_envelope_lines', store=True
    )
    disbursement_batch_line_ids = fields.One2many(
        'g2p.api.disbursement.batch.line', 'wizard_id', string='Disbursement Batches',
        compute='_compute_disbursement_batch_lines', store=True
    )
    # Visibility fields
    show_approve_enrolment_button = fields.Boolean(
        string="Show Approve Enrolment Button",
        compute="_compute_show_approve_enrolment_button",
        store=True
    )
    show_approve_disbursement_button = fields.Boolean(
        string="Show Approve Disbursement Button",
        compute="_compute_show_approve_disbursement_button",
        store=True
    )

    @api.depends('program_id', 'verification_ids')
    def _compute_show_approve_enrolment_button(self):
        for rec in self:
            show_button = False
            program = rec.program_id
            if program and program.verifications_for_enrolment is not None:
                try:
                    required_reviews = int(program.verifications_for_enrolment)
                except (ValueError, TypeError):
                    required_reviews = 0
                verification_count = len(rec.verification_ids)
                if verification_count >= required_reviews:
                    show_button = True
            rec.show_approve_enrolment_button = show_button
    
    @api.depends('program_id', 'verification_ids')
    def _compute_show_approve_disbursement_button(self):
        for rec in self:
            show_button = False
            program = rec.program_id
            if program and program.verifications_for_disbursement is not None:
                try:
                    required_reviews = int(program.verifications_for_disbursement)
                except (ValueError, TypeError):
                    required_reviews = 0
                verification_count = len(rec.verification_ids)
                if verification_count >= required_reviews:
                    show_button = True
            rec.show_approve_disbursement_button = show_button

    @api.depends('target_registry')
    def _compute_general_title(self):
        for rec in self:
            rec.general_title = 'General Statistics for %s' % rec.target_registry.capitalize()

    @api.depends('target_registry')
    def _compute_eligibility_group_title(self):
        for rec in self:
            rec.eligibility_group_title = 'Eligibility Statistics for %s' % rec.target_registry.capitalize()
    
    @api.depends('target_registry')
    def _compute_entitlement_group_title(self):
        for rec in self:
            rec.entitlement_group_title = 'Entitlement Statistics for %s' % rec.target_registry.capitalize()

    def _build_sql_query(self, odoo_domain, target_registry):
        sql_query=""
        order_by_field="id"
        try:
            if isinstance(odoo_domain, (list, tuple)):
                domain_value = odoo_domain
            elif isinstance(odoo_domain, str):
                domain_value = safe_eval(odoo_domain or "[]")
            else:
                domain_value = []
        except Exception as e:
            _logger.error(
                "Error evaluating domain: %s",
                e,
            )
            sql_query = ""
            return sql_query, order_by_field

        target_model_name = G2PTargetModelMapping.get_target_model_name(target_registry)

        if not target_model_name:
            _logger.error(
                "Unknown target_registry '%s'",
                target_registry,
            )
            sql_query = ""
            return sql_query, order_by_field

        target_model = self.env[target_model_name]

        try:
            query = target_model._where_calc(domain_value)
        except Exception as e:
            _logger.error(
                "Error calculating where clause for rule: %s", e
            )
            sql_query = ""
            return sql_query, order_by_field

        try:
            _, where_clause, where_clause_params = query.get_sql()
        except Exception as e:
            _logger.error(
                "Error generating SQL from query: %s", e
            )
            sql_query = ""
            return sql_query, order_by_field

        if not where_clause:
            return "", order_by_field

        where_str = "%s" % where_clause
        
        try:
            import re
            # Map Odoo's target_table."id" to target_table."link_registry_id" for external SR DB table compatibility
            where_str = re.sub(r'("g2p_[a_z_]+_registry")\."id"\b', r'\1."link_registry_id"', where_str)

            parts = where_str.split("%s")
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
                sql_query = "%s".join(new_parts) % tuple(formatted_params)
            else:
                formatted_params = list(map(lambda x: "'" + str(x).replace("'", "''") + "'" if isinstance(x, str) else str(x), where_clause_params))
                sql_query = where_str % tuple(formatted_params)
            _logger.info("Query: %s", sql_query)
        except Exception as e:
            _logger.error(
                "Error formatting query: %s",
                e,
            )
            sql_query = ""
        return sql_query, order_by_field

    @api.model
    def get_beneficiaries(self, wizard_id, page, page_size, odoo_domain):
        wizard = self.sudo().browse(wizard_id)
        api_url = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.staff_portal_api_url')
        sender_id = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.keymanager_sign_application_id')

        sql_query, order_by_condition = self._build_sql_query(odoo_domain, wizard.target_registry)

        response_json = None
        if api_url:
            endpoint = f"{api_url}/search_beneficiaries"
            now_ts = datetime.utcnow().isoformat() + "Z"
            header_data = {
                "version": "1.0.0",
                "message_id": "string",
                "message_ts": now_ts,
                "action": "search_beneficiaries",
                "sender_id": sender_id or "PBMS",
                "sender_uri": "",
                "receiver_id": "",
                "total_count": 0,
                "is_msg_encrypted": False,
                "meta": "string"
            }
            message_data = {
                "beneficiary_list_id": wizard.beneficiary_list_uuid,
                "target_registry": wizard.target_registry,
                "page": page,
                "page_size": page_size,
                "search_query": sql_query or "",
                "order_by": order_by_condition or "id asc",
            }
            request_header_data = {
                **header_data,
                "sender_app_mnemonic": "PBMS",
                "sender_app_url": "",
                "request_id": "string",
                "request_timestamp": now_ts,
            }
            request_body_data = {
                **message_data,
                "request_payload": message_data,
                "pagination_request": {
                    "search_text": sql_query or "",
                    "current_page": page,
                    "page_size": page_size,
                    "sort_by": order_by_condition if order_by_condition and order_by_condition != "id asc" and order_by_condition != "name" else "link_registry_id asc",
                },
            }
            payload = {
                "signature": "string",
                "header": header_data,
                "message": message_data,
                "request_header": request_header_data,
                "request_body": request_body_data,
            }

            try:
                jwt_token = self.env['keymanager.provider'].jwt_sign_keymanager(json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True))
                headers = {
                    "content-type": "application/json",
                    "Signature": jwt_token
                }
                response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
                response.raise_for_status()
                response_json = response.json()
                if "message" not in response_json and "response_body" in response_json:
                    payload_data = response_json.get("response_body", {}).get("response_payload", {})
                    response_json["message"] = payload_data
            except Exception as e:
                _logger.error("API call failed: %s", e)
                response_json = None

        if response_json and response_json.get("message", {}).get("beneficiaries"):
            msg = response_json.get("message", {})
            total_count = msg.get("total_beneficiary_count", 0)
            if total_count <= page_size:
                if wizard.beneficiary_list_id:
                    b_list = self.env['g2p.beneficiary.list'].sudo().browse(wizard.beneficiary_list_id)
                    if b_list and b_list.number_of_registrants and b_list.number_of_registrants > total_count:
                        msg["total_beneficiary_count"] = b_list.number_of_registrants
                elif wizard.target_registry:
                    target_model_name = G2PTargetModelMapping.get_target_model_name(wizard.target_registry)
                    if target_model_name:
                        try:
                            domain_val = odoo_domain if isinstance(odoo_domain, (list, tuple)) else safe_eval(odoo_domain or "[]")
                        except Exception:
                            domain_val = []
                        local_count = self.env[target_model_name].sudo().search_count(domain_val)
                        if local_count > total_count:
                            msg["total_beneficiary_count"] = local_count
            return response_json

        # Fallback search directly in Odoo registry model
        target_model_name = G2PTargetModelMapping.get_target_model_name(wizard.target_registry)
        if target_model_name:
            try:
                domain_val = odoo_domain if isinstance(odoo_domain, (list, tuple)) else safe_eval(odoo_domain or "[]")
            except Exception:
                domain_val = []
            target_model = self.env[target_model_name].sudo()
            total_count = target_model.search_count(domain_val)
            records = target_model.search(domain_val, offset=(page - 1) * page_size, limit=page_size)
            beneficiaries = []
            for rec in records:
                beneficiaries.append({
                    "id": rec.id,
                    "link_registry_id": getattr(rec, "link_registry_id", str(rec.id)),
                    "name": getattr(rec, "name", getattr(rec, "head_name", "")),
                    "household_id": getattr(rec, "household_id", ""),
                    "household_size": getattr(rec, "household_size", 0),
                    "head_name": getattr(rec, "head_name", ""),
                    "head_gender": (getattr(rec, "head_gender", "") or "").capitalize(),
                    "head_phone": getattr(rec, "head_phone", ""),
                    "head_dob": str(getattr(rec, "head_dob", "")) if getattr(rec, "head_dob", False) else "",
                    "gender": (getattr(rec, "gender", "") or "").capitalize(),
                    "institution_name": getattr(rec, "institution_name", ""),
                    "date_of_birth": str(getattr(rec, "date_of_birth", "")) if getattr(rec, "date_of_birth", False) else "",
                    "land_area": getattr(rec, "land_area", 0),
                    "no_of_cattle_heads": getattr(rec, "no_of_cattle_heads", 0),
                    "no_of_poultry_heads": getattr(rec, "no_of_poultry_heads", 0),
                    "annual_income": getattr(rec, "annual_income", 0),
                    "small_area_code": getattr(rec, "small_area_code", ""),
                    "large_area_code": getattr(rec, "large_area_code", ""),
                })
            return {
                "message": {
                    "total_beneficiary_count": total_count,
                    "page": page,
                    "page_size": page_size,
                    "beneficiaries": beneficiaries
                }
            }

        return {
            "message": {
                "total_beneficiary_count": 0,
                "page": page,
                "page_size": page_size,
                "beneficiaries": []
            }
        }

    @api.depends('target_registry')
    def _compute_summary_lines(self):
        excluded_keys = ['id', 'target_registry']
        for wizard in self:
            wizard.summary_line_ids = [(5, 0, 0)]
            api_url = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.staff_portal_api_url')
            sender_id = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.keymanager_sign_application_id')

            if not api_url:
                _logger.error("API_URL not set in environment")
            endpoint = f"{api_url}/summary"
            now_ts = datetime.utcnow().isoformat() + "Z"
            header_data = {
                "version": "1.0.0",
                "message_id": "string",
                "message_ts": now_ts,
                "action": "summary",
                "sender_id": sender_id or "PBMS",
                "sender_uri": "",
                "receiver_id": "",
                "total_count": 0,
                "is_msg_encrypted": False,
                "meta": "string"
            }
            message_data = {
                "beneficiary_list_id": wizard.beneficiary_list_uuid,
                "target_registry": wizard.target_registry
            }
            request_header_data = {
                **header_data,
                "sender_app_mnemonic": "PBMS",
                "sender_app_url": "",
                "request_id": "string",
                "request_timestamp": now_ts,
            }
            request_body_data = {
                **message_data,
                "request_payload": message_data,
            }
            payload = {
                "signature": "string",
                "header": header_data,
                "message": message_data,
                "request_header": request_header_data,
                "request_body": request_body_data,
            }

            jwt_token = self.env['keymanager.provider'].jwt_sign_keymanager(json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True))
            headers = {
                "content-type": "application/json",
                "Signature": jwt_token
            }
            try:
                response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
                response.raise_for_status()
                api_response = response.json()
                _logger.debug("API response: %s", api_response)
            except Exception as e:
                _logger.error("API call failed at summary API endpoint %s: %s" % (endpoint, str(e)))
                api_response = {
                    "message": {
                        "beneficiary_list_summary": {},
                        "registry_summary": {}
                    }
                }
            lines = []
            message = api_response.get('message', {})
            if not message and 'response_body' in api_response:
                payload_data = api_response.get('response_body', {}).get('response_payload', {})
                if 'summary' in payload_data:
                    message = payload_data.get('summary') or {}
                else:
                    message = payload_data
            if isinstance(message, dict) and 'summary' in message and message.get('summary'):
                message = message.get('summary')

            # Prepare benefit_code_id to mnemonic mapping
            benefit_code_obj = self.env['g2p.benefit.codes'].sudo()
            all_benefit_codes = benefit_code_obj.search([])
            benefit_code_id_to_mnemonic = {str(b.id): b.benefit_mnemonic for b in all_benefit_codes}
            benefit_code_id_to_unit = {str(b.id): b.measurement_unit for b in all_benefit_codes}

            # Flatten all keys from beneficiary_list_summary
            summary_dict = message.get('beneficiary_list_summary') or {}
            if not isinstance(summary_dict, dict):
                summary_dict = {}

            if wizard.program_id:
                summary_dict['program_id'] = wizard.program_id.id
                summary_dict['program_mnemonic'] = wizard.program_id.program_mnemonic
            if wizard.beneficiary_list_uuid:
                summary_dict['beneficiary_list_id'] = wizard.beneficiary_list_uuid

            try:
                ben_res = self.get_beneficiaries(wizard.id, 1, 1, None)
                if isinstance(ben_res, dict):
                    actual_total = ben_res.get('message', {}).get('total_beneficiary_count', 0)
                    if actual_total and actual_total > (summary_dict.get('number_of_registrants') or 0):
                        summary_dict['number_of_registrants'] = actual_total
            except Exception as cnt_err:
                _logger.error("Error checking total beneficiary count for summary: %s", cnt_err)

            if 'number_of_registrants' not in summary_dict or not summary_dict['number_of_registrants']:
                if wizard.beneficiary_list_id:
                    b_list = self.env['g2p.beneficiary.list'].sudo().browse(wizard.beneficiary_list_id)
                    if b_list and b_list.number_of_registrants:
                        summary_dict['number_of_registrants'] = b_list.number_of_registrants

            if isinstance(summary_dict, dict):
                for key, value in summary_dict.items():
                    if key in excluded_keys or value is None:
                        continue
                    if isinstance(value, dict):
                        for benefit_code_id, benefit_value in value.items():
                            if benefit_value is None:
                                continue
                            benefit_mnemonic = benefit_code_id_to_mnemonic.get(str(benefit_code_id), str(benefit_code_id))
                            measurement_unit = benefit_code_id_to_unit.get(str(benefit_code_id), "")
                            lines.append((0, 0, {
                                'wizard_id': wizard.id,
                                'key': f"{key.replace('_', ' ').title()} - {benefit_mnemonic}",
                                'value': f"{'{:,}'.format(int(benefit_value)) if isinstance(benefit_value, (int, float)) else str(benefit_value)} {measurement_unit}".strip(),
                                'summary_type': 'entitlement'
                            }))
                    else:
                        val = value
                        if key == 'program_mnemonic' and wizard.program_id and wizard.program_id.program_mnemonic:
                            val = wizard.program_id.program_mnemonic
                        elif key == 'program_id' and wizard.program_id and wizard.program_id.id:
                            val = wizard.program_id.id
                        lines.append((0, 0, {
                            'wizard_id': wizard.id,
                            'key': key.replace('_', ' ').title(),
                            'value': '{:,}'.format(int(val)) if isinstance(val, (int, float)) else str(val),
                            'summary_type': 'general'
                        }))

            # Flatten all keys from registry_summary
            registry_dict = message.get('registry_summary') or {}
            if not isinstance(registry_dict, dict):
                registry_dict = {}

            if (wizard.target_registry or '').lower() == 'household':
                total_m = registry_dict.get('total_male_heads', 0) or 0
                total_f = registry_dict.get('total_female_heads', 0) or 0
                avg_sz = registry_dict.get('average_household_size', 0.0) or 0.0
                reg_count = summary_dict.get('number_of_registrants', 0) or 0
                if (not total_m and not total_f and not avg_sz) or (reg_count > 0 and (total_m + total_f) > reg_count):
                    try:
                        ben_res = self.get_beneficiaries(wizard.id, 1, 10000, None)
                        b_list = ben_res.get('message', {}).get('beneficiaries', []) if isinstance(ben_res, dict) else []
                    except Exception as b_err:
                        _logger.error("Error fetching beneficiaries for stats fallback: %s", b_err)
                        b_list = []
                    
                    if b_list:
                        m_cnt = sum(1 for b in b_list if (str(b.get('head_gender') or '')).lower().startswith('m'))
                        f_cnt = sum(1 for b in b_list if (str(b.get('head_gender') or '')).lower().startswith('f'))
                        sizes = [float(b.get('household_size') or 0) for b in b_list if b.get('household_size') is not None]
                        calc_avg = round(sum(sizes) / len(b_list), 2) if b_list else 0.0
                        registry_dict['total_male_heads'] = m_cnt
                        registry_dict['total_female_heads'] = f_cnt
                        registry_dict['average_household_size'] = calc_avg

            if isinstance(registry_dict, dict):
                for key, value in registry_dict.items():
                    if key in excluded_keys or value is None:
                        continue
                    if isinstance(value, dict):
                        for benefit_code_id, benefit_value in value.items():
                            if benefit_value is None:
                                continue
                            benefit_mnemonic = benefit_code_id_to_mnemonic.get(str(benefit_code_id), str(benefit_code_id))
                            measurement_unit = benefit_code_id_to_unit.get(str(benefit_code_id), "")
                            lines.append((0, 0, {
                                'wizard_id': wizard.id,
                                'key': f"{key.replace('_', ' ').title()} - {benefit_mnemonic}",
                                'value': f"{'{:,}'.format(int(benefit_value)) if isinstance(benefit_value, (int, float)) else str(benefit_value)} {measurement_unit}".strip(),
                                'summary_type': 'entitlement'
                            }))
                    else:
                        lines.append((0, 0, {
                            'wizard_id': wizard.id,
                            'key': key.replace('_', ' ').title(),
                            'value': '{:,}'.format(int(value)) if isinstance(value, (int, float)) else str(value),
                            'summary_type': 'eligibility'
                        }))

            # Compute entitlement statistics from program benefit codes if missing
            entitlement_lines = [l for l in lines if l[2].get('summary_type') == 'entitlement']
            if not entitlement_lines and wizard.program_id:
                program_benefits = self.env['g2p.program.benefit.codes'].sudo().search([('program_id', '=', wizard.program_id.id)])
                reg_count = summary_dict.get('number_of_registrants', 0) or 0
                for p_ben in program_benefits:
                    b_mnemonic = p_ben.benefit_mnemonic or (p_ben.benefit_code_id.benefit_mnemonic if p_ben.benefit_code_id else "Benefit")
                    unit = p_ben.measurement_unit or (p_ben.benefit_code_id.measurement_unit if p_ben.benefit_code_id else "")
                    max_q = p_ben.max_quantity or 0.0
                    tot_q = reg_count * max_q
                    formatted_tot = f"{'{:,}'.format(int(tot_q)) if isinstance(tot_q, (int, float)) and tot_q == int(tot_q) else str(tot_q)} {unit}".strip()
                    formatted_per = f"{'{:,}'.format(int(max_q)) if isinstance(max_q, (int, float)) and max_q == int(max_q) else str(max_q)} {unit}".strip()
                    lines.append((0, 0, {
                        'wizard_id': wizard.id,
                        'key': f"Total Entitlement - {b_mnemonic}",
                        'value': formatted_tot,
                        'summary_type': 'entitlement'
                    }))
                    lines.append((0, 0, {
                        'wizard_id': wizard.id,
                        'key': f"Disbursement Per Beneficiary - {b_mnemonic}",
                        'value': formatted_per,
                        'summary_type': 'entitlement'
                    }))

            wizard.summary_line_ids = lines

    @api.depends('list_stage', 'beneficiary_list_uuid')
    def _compute_disbursement_envelope_lines(self):
        for wizard in self:
            wizard.disbursement_envelope_line_ids = [(5, 0, 0)]
            if (wizard.list_stage or '').lower() != 'disbursement' or not wizard.beneficiary_list_uuid:
                continue
            api_url = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.staff_portal_api_url')
            sender_id = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.keymanager_sign_application_id')

            if not api_url:
                _logger.error("API_URL not set in environment")
                continue
            endpoint = f"{api_url}/disbursement_envelope"
            payload = {
                "signature": "string",
                "header": {
                    "version": "1.0.0",
                    "message_id": "string",
                    "message_ts": "string",
                    "action": "disbursement_envelope",
                    "sender_id": sender_id,
                    "sender_uri": "",
                    "receiver_id": "",
                    "total_count": 0,
                    "is_msg_encrypted": False,
                    "meta": "string"
                },
                "message": {
                    "beneficiary_list_id": wizard.beneficiary_list_uuid
                }
            }

            jwt_token = self.env['keymanager.provider'].jwt_sign_keymanager(json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True))
            headers = {
                "content-type": "application/json",
                "Signature": jwt_token
            }
            try:
                response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
                response.raise_for_status()
                api_response = response.json()
                _logger.debug("Disbursement Envelope API response: %s", api_response)
            except Exception as e:
                _logger.error("Disbursement Envelope API call failed: %s", e)
                continue
            message = api_response.get('message', {})
            envelope_list = message.get('disbursement_envelopes', [])
            lines = []
            # Use the model's _fields attribute via env, not via the class directly
            envelope_model = self.env['g2p.api.disbursement.envelope.line']
            envelope_fields = envelope_model._fields.keys()
            for envelope in envelope_list:
                vals = {
                    'wizard_id': wizard.id,
                    'disbursement_envelope_id': envelope.get('id')
                }
                for field in envelope_fields:
                    if field not in ('disbursement_envelope_id', 'wizard_id'):
                        vals[field] = envelope.get(field)
                lines.append((0, 0, vals))
            wizard.disbursement_envelope_line_ids = lines

    @api.depends('list_stage', 'beneficiary_list_uuid')
    def _compute_disbursement_batch_lines(self):
        for wizard in self:
            wizard.disbursement_batch_line_ids = [(5, 0, 0)]
            if (wizard.list_stage or '').lower() != 'disbursement' or not wizard.beneficiary_list_uuid:
                continue
            api_url = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.staff_portal_api_url')
            sender_id = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.keymanager_sign_application_id')

            if not api_url:
                _logger.error("API_URL not set in environment")
                continue
            endpoint = f"{api_url}/disbursement_batch"
            payload = {
                "signature": "string",
                "header": {
                    "version": "1.0.0",
                    "message_id": "string",
                    "message_ts": "string",
                    "action": "disbursement_batch",
                    "sender_id": sender_id,
                    "sender_uri": "",
                    "receiver_id": "",
                    "total_count": 0,
                    "is_msg_encrypted": False,
                    "meta": "string"
                },
                "message": {
                    "beneficiary_list_id": wizard.beneficiary_list_uuid
                }
            }

            jwt_token = self.env['keymanager.provider'].jwt_sign_keymanager(json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True))
            headers = {
                "content-type": "application/json",
                "Signature": jwt_token
            }
            try:
                response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
                response.raise_for_status()
                api_response = response.json()
                _logger.debug("Disbursement Batch API response: %s", api_response)
            except Exception as e:
                _logger.error("Disbursement Batch API call failed: %s", e)
                continue
            message = api_response.get('message', {})
            batch_list = message.get('disbursement_batches', [])
            lines = []
            # Use the model's _fields attribute via env, not via the class directly
            batch_model = self.env['g2p.api.disbursement.batch.line']
            batch_fields = batch_model._fields.keys()
            for batch in batch_list:
                vals = {
                    'wizard_id': wizard.id,
                    'batch_id': batch.get('id')
                }
                for field in batch_fields:
                    if field not in ('batch_id', 'wizard_id'):
                        vals[field] = batch.get(field)
                lines.append((0, 0, vals))
            wizard.disbursement_batch_line_ids = lines

    @api.depends('summary_line_ids')
    def _compute_general(self):
        for wizard in self:
            wizard.summary_general_line_ids = wizard.summary_line_ids.filtered(
                lambda r: r.summary_type == 'general'
            )

    @api.depends('summary_line_ids')
    def _compute_eligibility(self):
        for wizard in self:
            wizard.summary_eligibility_line_ids = wizard.summary_line_ids.filtered(
                lambda r: r.summary_type == 'eligibility'
            )
    
    @api.depends('summary_line_ids')
    def _compute_entitlement(self):
        for wizard in self:
            wizard.summary_entitlement_line_ids = wizard.summary_line_ids.filtered(
                lambda r: r.summary_type == 'entitlement'
            )

    @api.depends('beneficiary_list_id')
    def _compute_verification_ids(self):
        for wizard in self:
            if wizard.beneficiary_list_id:
                verifications = self.env['storage.file'].search([
                    ('beneficiary_list_id', '=', wizard.beneficiary_list_id)
                ])
                wizard.verification_ids = verifications.ids if verifications else None
            else:
                wizard.verification_ids = [(5, 0, 0)]

    def approve_final_enrollment(self):
        self.ensure_one()
        if not self.list_workflow_status == 'approved_final_enrolment':
            self.list_workflow_status = 'approved_final_enrolment'
            self.approved_for_enrollment = True
            self.env['g2p.beneficiary.list'].browse(self.beneficiary_list_id).write({
                'list_workflow_status': 'approved_final_enrolment',
                'approval_date': fields.Date.context_today(self)
            })
            self.env['g2p.enrollment.cycle'].browse(self.enrollment_cycle_id).write({
                'approved_for_enrollment': True,
            })

    def action_approve_final_enrollment(self):
        allowed_group = 'g2p_pbms.group_enrolment_approver'
        if not self.env.user.has_group(allowed_group):
            raise AccessError(_("You are not allowed to perform this action."))

        self.approve_final_enrollment()
    
    def approve_for_disbursement(self):
        self.ensure_one()
        if not self.list_workflow_status == 'approved_for_disbursement':
            self.list_workflow_status = 'approved_for_disbursement'
            self.approved_for_disbursement = True
            self.env['g2p.beneficiary.list'].browse(self.beneficiary_list_id).write({
                'list_workflow_status': 'approved_for_disbursement',
                'approval_date': fields.Date.context_today(self),
                'envelope_creation_status': 'pending'
            })
            self.env['g2p.disbursement.cycle'].browse(self.disbursement_cycle_id).write({
                'approved_for_disbursement': True,
            })

    def action_approve_for_disbursement(self):
        allowed_group = 'g2p_pbms.group_disbursement_approver'
        if not self.env.user.has_group(allowed_group):
            raise AccessError(_("You are not allowed to perform this action."))
        
        self.approve_for_disbursement()

    def action_record_verifications(self):
        allowed_group = 'g2p_pbms.group_beneficiary_list_verifier'
        if not self.env.user.has_group(allowed_group):
            raise AccessError(_("You are not allowed to perform this action."))
        
        program = self.program_id
        if not program:
            raise UserError(_("No program is linked to this record."))

        if self.list_stage == 'enrollment' and self.list_workflow_status!='approved_final_enrolment' and program.auto_approve_enrolment:
            self.approve_final_enrollment(self)
        elif self.list_stage == 'disbursement' and self.list_workflow_status!='approved_for_disbursement' and program.auto_approve_disbursement:
            self.approve_for_disbursement(self)
        
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Add a Verification',
            'res_model': 'storage.file',
            'view_mode': 'form',
            'view_id': self.env.ref('g2p_pbms.view_g2p_beneficiary_list_verification_form').id,
            'target': 'new',
            'context': {
                'default_beneficiary_list_id': self.beneficiary_list_id,
                'beneficiary_list_verification_form_edit': True,
            },
        }

class G2PAPISummaryLine(models.TransientModel):
    _name = 'g2p.api.summary.line'
    _description = 'Dynamic API Summary Line'

    wizard_id = fields.Many2one('g2p.bgtask.summary.wizard', string='Wizard')
    key = fields.Char(string='Field', required=False)
    value = fields.Text(string='Value', required=False)
    summary_type = fields.Selection(
        [('general', 'General'), ('entitlement', 'Entitlement'), ('eligibility', 'Eligibility')],
        string="Summary Type",
        default='general'
    )

class G2PAPIDisbursementEnvelopeLine(models.TransientModel):
    _name = 'g2p.api.disbursement.envelope.line'
    _description = 'Disbursement Envelope Line'

    wizard_id = fields.Many2one('g2p.bgtask.summary.wizard', string='Wizard')
    disbursement_envelope_id = fields.Char(string='Disbursement Envelope ID')
    beneficiary_list_id = fields.Char(string='Beneficiary List ID')
    benefit_code_id = fields.Integer(string='Benefit Code ID')
    benefit_code_mnemonic = fields.Char(
        string='Benefit Code Mnemonic',
        compute='_compute_benefit_code_mnemonic',
        store=False
    )
    benefit_type = fields.Char(string='Benefit Type')
    benefit_program_mnemonic = fields.Char(string='Benefit Program Mnemonic')
    disbursement_cycle_id = fields.Char(string='Disbursement Cycle ID')
    cycle_code_mnemonic = fields.Char(string='Cycle Code Mnemonic')
    number_of_beneficiaries = fields.Integer(string='Number of Beneficiaries')
    number_of_disbursements = fields.Integer(string='Number of Disbursements')
    total_disbursement_quantity = fields.Float(string='Total Disbursement Quantity')
    measurement_unit = fields.Char(string='Measurement Unit')

    @api.depends('benefit_code_id')
    def _compute_benefit_code_mnemonic(self):
        for rec in self:
            mnemonic = False
            if rec.benefit_code_id:
                benefit_code = self.env['g2p.benefit.codes'].search([('id', '=', rec.benefit_code_id)], limit=1)
                mnemonic = benefit_code.benefit_mnemonic if benefit_code else False
            rec.benefit_code_mnemonic = mnemonic

    def name_get(self):
        res = []
        for rec in self:
            name = f"{rec.benefit_program_mnemonic or ''} / {rec.cycle_code_mnemonic or ''}"
            res.append((rec.id, name))
        return res

    def action_view_disbursement_envelope(self):
        self.ensure_one()
        try:
            api_url = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.g2p_bridge_api_url')
            sender_id = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.keymanager_sign_application_id')

            if not api_url:
                _logger.error("Bridge API URL not set in environment")
            endpoint = f"{api_url}/get_disbursement_envelope_status"
            payload = {
                "header": {
                    "version": "1.0.0",
                    "message_id": "string",
                    "message_ts": "string",
                    "action": "get_disbursement_envelope_status",
                    "sender_id": sender_id,
                    "sender_uri": "",
                    "receiver_id": "",
                    "total_count": 0,
                    "is_msg_encrypted": False,
                    "meta": "string"
                },
                "message": self.disbursement_envelope_id,
            }

            jwt_token = self.env['keymanager.provider'].jwt_sign_keymanager(json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True))
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Signature": jwt_token
            }
            try:
                response = requests.post(endpoint, json=payload, timeout=10, headers=headers)
                response.raise_for_status()
                resp_data = response.json()
                _logger.debug("Disbursement ENvelope Status Response: %s", resp_data)

            except Exception as e:
                _logger.error("API call failed: %s", e)
                return

            message = resp_data.get("message", {})

            summary_vals = {
                "wizard_id": self.wizard_id.id if self.wizard_id else False,
                "disbursement_envelope_id": message.get("disbursement_envelope_id"),
                "benefit_code_id": self.benefit_code_id,
                "benefit_code_mnemonic": message.get("benefit_code_mnemonic"),
                "benefit_type": message.get("benefit_type"),
                "measurement_unit": message.get("measurement_unit"),
                "number_of_beneficiaries_received": message.get("number_of_beneficiaries_received"),
                "number_of_beneficiaries_declared": message.get("number_of_beneficiaries_declared"),
                "number_of_disbursements_declared": message.get("number_of_disbursements_declared"),
                "number_of_disbursements_received": message.get("number_of_disbursements_received"),
                "total_disbursement_quantity_declared": (
                    "{:,}".format(int(message.get("total_disbursement_quantity_declared", 0)))
                    + (" " + str(message.get("measurement_unit", "")) if message.get("measurement_unit") else "")
                    if message.get("total_disbursement_quantity_declared") is not None else ""
                ),
                "total_disbursement_quantity_received": (
                    "{:,}".format(int(message.get("total_disbursement_quantity_received", 0)))
                    + (" " + str(message.get("measurement_unit", "")) if message.get("measurement_unit") else "")
                    if message.get("total_disbursement_quantity_received") is not None else ""
                ),
                "funds_available_with_bank": message.get("funds_available_with_bank"),
                "funds_available_latest_timestamp": self.odoo_datetime_format(message.get("funds_available_latest_timestamp")),
                "funds_available_latest_error_code": message.get("funds_available_latest_error_code"),
                "funds_available_attempts": message.get("funds_available_attempts"),
                "funds_blocked_with_bank": message.get("funds_blocked_with_bank"),
                "funds_blocked_latest_timestamp": self.odoo_datetime_format(message.get("funds_blocked_latest_timestamp")),
                "funds_blocked_latest_error_code": message.get("funds_blocked_latest_error_code"),
                "funds_blocked_attempts": message.get("funds_blocked_attempts"),
                "funds_blocked_reference_number": message.get("funds_blocked_reference_number"),
                "number_of_disbursements_shipped": message.get("number_of_disbursements_shipped"),
                "number_of_disbursements_reconciled": message.get("number_of_disbursements_reconciled"),
                "number_of_disbursements_reversed": message.get("number_of_disbursements_reversed"),
                "no_of_warehouses_allocated": message.get("no_of_warehouses_allocated"),
                "no_of_warehouses_notified": message.get("no_of_warehouses_notified"),
                "no_of_agencies_allocated": message.get("no_of_agencies_allocated"),
                "no_of_agencies_notified": message.get("no_of_agencies_notified"),
                "no_of_beneficiaries_notified": message.get("no_of_beneficiaries_notified"),
                "no_of_pods_received": message.get("no_of_pods_received"),
            }

            geo_lines = []
            disbursement_batch_control_geos = message.get("disbursement_batch_control_geos", None)
            if disbursement_batch_control_geos:
                for geo in disbursement_batch_control_geos:
                    geo_lines.append((0, 0, {
                        "disbursement_batch_control_geo_id": geo.get("disbursement_batch_control_geo_id"),
                        "disbursement_cycle_id": geo.get("disbursement_cycle_id"),
                        "disbursement_envelope_id": geo.get("disbursement_envelope_id"),
                        "disbursement_batch_control_id": geo.get("disbursement_batch_control_id"),
                        "administrative_zone_id_large": geo.get("administrative_zone_id_large"),
                        "administrative_zone_mnemonic_large": geo.get("administrative_zone_mnemonic_large"),
                        "administrative_zone_id_small": geo.get("administrative_zone_id_small"),
                        "administrative_zone_mnemonic_small": geo.get("administrative_zone_mnemonic_small"),
                        "no_of_beneficiaries": geo.get("no_of_beneficiaries"),
                        "total_quantity": geo.get("total_quantity"),
                        "warehouse_id": geo.get("warehouse_id"),
                        "warehouse_mnemonic": geo.get("warehouse_mnemonic"),
                        "warehouse_additional_attributes": geo.get("warehouse_additional_attributes"),
                        "agency_id": geo.get("agency_id"),
                        "agency_mnemonic": geo.get("agency_mnemonic"),
                        "agency_additional_attributes": geo.get("agency_additional_attributes"),
                        "warehouse_notification_status": geo.get("warehouse_notification_status"),
                        "agency_notification_status": geo.get("agency_notification_status"),
                    }))
            if geo_lines:
                summary_vals["disbursement_envelope_summary_geo_ids"] = geo_lines

            summary = self.env["g2p.disbursement.envelope.summary.wizard"].create(summary_vals)

            return self.env.ref("g2p_pbms.action_generate_disbursement_envelope_summary").report_action(summary)


        except requests.exceptions.RequestException as e:
            raise UserError("Failed to fetch disbursement envelope status: %s" % e) from e
        except Exception as e:
            raise UserError("An error occurred: %s" % e) from e
        
    # Try parsing ISO 8601 with microseconds
    def odoo_datetime_format(self, dt_str):
        try:
            dt = datetime.fromisoformat(dt_str)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        # Fallback
        except Exception:
            return dt_str

class G2PAPIDisbursementBatchLine(models.TransientModel):
    _name = 'g2p.api.disbursement.batch.line'
    _description = 'Disbursement Batch Line'

    wizard_id = fields.Many2one('g2p.bgtask.summary.wizard', string='Wizard')

    batch_id = fields.Char(string='Batch ID')
    beneficiary_list_id = fields.Char(string='Beneficiary List ID')
    disbursement_cycle_id = fields.Char(string='Disbursement Cycle ID')
    beneficiary_list_details_id = fields.Char(string='Beneficiary List Details ID')
    disbursement_envelope_id = fields.Char(string='Disbursement Envelope ID')
    disbursements = fields.Text(string='Disbursements')
    disbursement_status = fields.Char(string='Batch Status')
    benefit_code_id = fields.Integer(string='Benefit Code ID')
    benefit_code_mnemonic = fields.Char(
        string='Benefit Code Mnemonic',
        compute='_compute_benefit_code_mnemonic',
        store=False
    )
    measurement_unit = fields.Char(string='Measurement Unit')
    number_of_beneficiaries = fields.Integer(string='Number of Beneficiaries')
    number_of_disbursements = fields.Integer(string='Number of Disbursements')
    total_disbursement_quantity = fields.Float(string='Total Disbursement Quantity')

    @api.depends('benefit_code_id')
    def _compute_benefit_code_mnemonic(self):
        for rec in self:
            mnemonic = False
            if rec.benefit_code_id:
                benefit_code = self.env['g2p.benefit.codes'].search([('id', '=', rec.benefit_code_id)], limit=1)
                mnemonic = benefit_code.benefit_mnemonic if benefit_code else False
            rec.benefit_code_mnemonic = mnemonic

    def name_get(self):
        res = []
        for rec in self:
            name = f"{rec.benefit_program_mnemonic or ''} / {rec.cycle_code_mnemonic or ''} / {rec.batch_code or ''}"
            res.append((rec.id, name))
        return res

    def action_view_disbursement_batch(self):
        self.ensure_one()
        try:
            api_url = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.g2p_bridge_api_url')
            sender_id = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.keymanager_sign_application_id')

            if not api_url:
                _logger.error("Bridge API URL not set in environment")
            endpoint = f"{api_url}/get_disbursement_batch_control"
            payload = {
                "header": {
                    "version": "1.0.0",
                    "message_id": "string",
                    "message_ts": "string",
                    "action": "get_disbursement_batch_control",
                    "sender_id": sender_id,
                    "sender_uri": "",
                    "receiver_id": "",
                    "total_count": 0,
                    "is_msg_encrypted": False,
                    "meta": "string"
                },
                "message": self.batch_id,
            }

            jwt_token = self.env['keymanager.provider'].jwt_sign_keymanager(json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True))
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Signature": jwt_token
            }
            try:
                response = requests.post(endpoint, json=payload, timeout=10, headers=headers)
                response.raise_for_status()
                resp_data = response.json()
                _logger.info("Disbursement Batch Status Response: %s", resp_data)
            except Exception as e:
                _logger.error("API call failed: %s", e)
                return

            message = resp_data.get("message", {})

            summary_vals = {
                "wizard_id": self.wizard_id.id if self.wizard_id else False,
                "disbursement_batch_control_id": message.get("disbursement_batch_control_id"),
                "disbursement_cycle_id": message.get("disbursement_cycle_id"),
                "disbursement_cycle_code_mnemonic": message.get("disbursement_cycle_code_mnemonic"),
                "disbursement_envelope_id": message.get("disbursement_envelope_id"),
                "benefit_code_id": message.get("benefit_code_id"),
                "benefit_code_mnemonic": message.get("benefit_code_mnemonic"),
                "benefit_type": message.get("benefit_type"),
                "measurement_unit": message.get("measurement_unit"),
                "fa_resolution_status": message.get("fa_resolution_status"),
                "fa_resolution_timestamp": self.odoo_datetime_format(message.get("fa_resolution_timestamp")),
                "fa_resolution_latest_error_code": message.get("fa_resolution_latest_error_code"),
                "fa_resolution_attempts": message.get("fa_resolution_attempts"),
                "sponsor_bank_dispatch_status": message.get("sponsor_bank_dispatch_status"),
                "sponsor_bank_dispatch_timestamp": self.odoo_datetime_format(message.get("sponsor_bank_dispatch_timestamp")),
                "sponsor_bank_dispatch_latest_error_code": message.get("sponsor_bank_dispatch_latest_error_code"),
                "sponsor_bank_dispatch_attempts": message.get("sponsor_bank_dispatch_attempts"),
                "geo_resolution_status": message.get("geo_resolution_status"),
                "geo_resolution_timestamp": self.odoo_datetime_format(message.get("geo_resolution_timestamp")),
                "geo_resolution_latest_error_code": message.get("geo_resolution_latest_error_code"),
                "geo_resolution_attempts": message.get("geo_resolution_attempts"),
                "warehouse_allocation_status": message.get("warehouse_allocation_status"),
                "warehouse_allocation_timestamp": self.odoo_datetime_format(message.get("warehouse_allocation_timestamp")),
                "warehouse_allocation_latest_error_code": message.get("warehouse_allocation_latest_error_code"),
                "warehouse_allocation_attempts": message.get("warehouse_allocation_attempts"),
                "agency_allocation_status": message.get("agency_allocation_status"),
                "agency_allocation_timestamp": self.odoo_datetime_format(message.get("agency_allocation_timestamp")),
                "agency_allocation_latest_error_code": message.get("agency_allocation_latest_error_code"),
                "agency_allocation_attempts": message.get("agency_allocation_attempts"),
            }

            geo_lines = []
            disbursement_batch_control_geos = message.get("disbursement_batch_control_geos", None)
            if disbursement_batch_control_geos:
                for geo in disbursement_batch_control_geos:
                    geo_lines.append((0, 0, {
                        "disbursement_batch_control_geo_id": geo.get("disbursement_batch_control_geo_id"),
                        "disbursement_cycle_id": geo.get("disbursement_cycle_id"),
                        "disbursement_envelope_id": geo.get("disbursement_envelope_id"),
                        "disbursement_batch_control_id": geo.get("disbursement_batch_control_id"),
                        "administrative_zone_id_large": geo.get("administrative_zone_id_large"),
                        "administrative_zone_mnemonic_large": geo.get("administrative_zone_mnemonic_large"),
                        "administrative_zone_id_small": geo.get("administrative_zone_id_small"),
                        "administrative_zone_mnemonic_small": geo.get("administrative_zone_mnemonic_small"),
                        "no_of_beneficiaries": geo.get("no_of_beneficiaries"),
                        "total_quantity": geo.get("total_quantity"),
                        "warehouse_id": geo.get("warehouse_id"),
                        "warehouse_mnemonic": geo.get("warehouse_mnemonic"),
                        "warehouse_additional_attributes": geo.get("warehouse_additional_attributes"),
                        "agency_id": geo.get("agency_id"),
                        "agency_mnemonic": geo.get("agency_mnemonic"),
                        "agency_additional_attributes": geo.get("agency_additional_attributes"),
                        "warehouse_notification_status": geo.get("warehouse_notification_status"),
                        "agency_notification_status": geo.get("agency_notification_status"),
                    }))
            if geo_lines:
                summary_vals["disbursement_batch_summary_geo_ids"] = geo_lines

            summary = self.env["g2p.disbursement.batch.summary.wizard"].create(summary_vals)

            return self.env.ref("g2p_pbms.action_generate_disbursement_batch_summary").report_action(summary)

        except requests.exceptions.RequestException as e:
            raise UserError("Failed to fetch disbursement batch status: %s" % e) from e
        except Exception as e:
            raise UserError("An error occurred: %s" % e) from e

    # Try parsing ISO 8601 with microseconds
    def odoo_datetime_format(self, dt_str):
        try:
            dt = datetime.fromisoformat(dt_str)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        # Fallback
        except Exception:
            return dt_str