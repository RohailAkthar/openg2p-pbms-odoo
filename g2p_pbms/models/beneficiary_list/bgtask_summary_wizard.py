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
from openg2p_fastapi_common.schemas import G2PRequestHeader, G2PPaginationRequest
from openg2p_g2p_bridge_models.schemas import (
    DisbursementEnvelopeStatusRequest,
    DisbursementEnvelopeStatusRequestBody,
    DisbursementEnvelopeStatusResponse,
    DisbursementBatchControlRequest,
    DisbursementBatchControlRequestBody,
    DisbursementBatchControlResponse,
)
from openg2p_bg_task_models.schemas import (
    BeneficiarySearchRequest,
    BeneficiarySearchRequestBody,
    BeneficiarySearchRequestPayload,
    SummaryRequest,
    SummaryRequestBody,
    SummaryRequestPayload,
    DisbursementEnvelopeRequest,
    DisbursementEnvelopeRequestBody,
    DisbursementEnvelopeRequestPayload,
    DisbursementBatchRequest,
    DisbursementBatchRequestBody,
    DisbursementBatchRequestPayload,
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
    disbursement_cycle_m2o_id = fields.Many2one("g2p.disbursement.cycle", string="Disbursement Cycle Record")
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
    summary_eligibility_line_ids = fields.One2many(
        'g2p.api.summary.line', 'wizard_id', string='Registry Info',
        compute='_compute_eligibility'
    )
    summary_entitlement_line_ids = fields.One2many(
        'g2p.api.summary.line', 'wizard_id', string='Registry Info',
        compute='_compute_entitlement'
    )
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
    )
    show_approve_disbursement_button = fields.Boolean(
        string="Show Approve Disbursement Button",
        compute="_compute_show_approve_disbursement_button",
    )

    # --- Cycle context fields (populated from cycle when opened via action_open_view) ---
    cycle_name = fields.Char(string="Cycle #")
    cycle_created_on = fields.Datetime(string="Cycle Created On")
    cycle_created_by = fields.Many2one("res.users", string="Cycle Created By")
    current_version = fields.Integer(string="Current Version")
    current_stage_display = fields.Char(string="Current Stage")
    current_approval_status = fields.Selection(
        [("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")],
        string="Status",
    )
    current_acted_at = fields.Datetime(string="Acted On")
    current_acted_by = fields.Many2one("res.users", string="Acted By")
    current_enqueued_at = fields.Datetime(string="Queued On")
    current_beneficiary_count = fields.Integer(string="# of Beneficiaries")
    eligibility_process_status = fields.Char(string="Resolution Status")
    can_create_list = fields.Boolean(string="Can Create Version", default=False)

    # --- Bridge Status fields ---
    # bridge_dispatch_status = fields.Char(string="Dispatch Status")
    envelope_status = fields.Char(string="Envelope Status")
    disbursement_batch_status = fields.Char(string="Batch Status")
    bridge_envelope_count = fields.Integer(
        string="# of Envelopes",
        compute="_compute_bridge_counts",
    )
    bridge_batch_count = fields.Integer(
        string="# of Batches",
        compute="_compute_bridge_counts",
    )

    # --- Extra Beneficiary fields ---
    computation_status = fields.Char(string="Computation Status")
    total_entitlements = fields.Integer(string="Total Entitlements")

    # --- Approval queue context ---
    pending_stage_id = fields.Many2one("g2p.workflow.pending.stage", string="Pending Stage")
    show_approval_buttons = fields.Boolean(
        string="Show Approval Buttons",
        compute="_compute_show_approval_buttons",
    )

    @api.depends("pending_stage_id", "current_approval_status")
    def _compute_show_approval_buttons(self):
        for rec in self:
            rec.show_approval_buttons = bool(
                rec.pending_stage_id and rec.current_approval_status == "PENDING"
            )

    @api.depends("disbursement_envelope_line_ids", "disbursement_batch_line_ids")
    def _compute_bridge_counts(self):
        for rec in self:
            rec.bridge_envelope_count = len(rec.disbursement_envelope_line_ids)
            rec.bridge_batch_count = len(rec.disbursement_batch_line_ids)

    def action_wizard_approve(self):
        self.ensure_one()
        if self.pending_stage_id:
            return self.pending_stage_id.action_approve()
        raise UserError("No pending stage associated.")

    def action_wizard_reject(self):
        self.ensure_one()
        if self.pending_stage_id:
            return self.pending_stage_id.action_reject()
        raise UserError("No pending stage associated.")

    # --- Approval History: stage history of the current list ---
    stage_history_ids = fields.Many2many(
        "g2p.workflow.stage.history",
        compute="_compute_workflow_data",
        string="Approval History",
    )
    # --- Previous Versions: other lists in the same cycle ---
    previous_list_ids = fields.Many2many(
        "g2p.beneficiary.list",
        compute="_compute_workflow_data",
        string="Previous Versions",
    )
    has_previous_versions = fields.Boolean(
        compute="_compute_workflow_data",
        string="Has Previous Versions",
    )
    # Cycle pagination navigation — disbursement
    prev_disbursement_cycle_id = fields.Many2one(
        "g2p.disbursement.cycle",
        compute="_compute_disbursement_cycle_nav",
        store=False,
    )
    next_disbursement_cycle_id = fields.Many2one(
        "g2p.disbursement.cycle",
        compute="_compute_disbursement_cycle_nav",
        store=False,
    )
    is_viewing_current_disbursement_cycle = fields.Boolean(
        compute="_compute_disbursement_cycle_nav",
        store=False,
    )
    # Cycle pagination navigation — enrollment
    prev_enrollment_cycle_id = fields.Many2one(
        "g2p.enrollment.cycle",
        compute="_compute_enrollment_cycle_nav",
        store=False,
    )
    next_enrollment_cycle_id = fields.Many2one(
        "g2p.enrollment.cycle",
        compute="_compute_enrollment_cycle_nav",
        store=False,
    )
    is_viewing_current_enrollment_cycle = fields.Boolean(
        compute="_compute_enrollment_cycle_nav",
        store=False,
    )
    # Relay field so we can show and edit the disbursement cycle's priority rules in the wizard
    priority_rule_ids = fields.One2many(
        "g2p.priority.rule.definition",
        related="disbursement_cycle_m2o_id.priority_rule_ids",
        string="Disbursement Rules",
        readonly=False,
    )

    @api.depends("beneficiary_list_id", "enrollment_cycle_id", "disbursement_cycle_id")
    def _compute_workflow_data(self):
        BeneficiaryList = self.env["g2p.beneficiary.list"]
        for rec in self:
            lst = BeneficiaryList.browse(rec.beneficiary_list_id) if rec.beneficiary_list_id else BeneficiaryList
            rec.stage_history_ids = lst.stage_history_ids if lst.exists() else self.env["g2p.workflow.stage.history"]
            # Previous lists: all lists in the cycle except current
            if rec.enrollment_cycle_id:
                cycle = self.env["g2p.enrollment.cycle"].browse(rec.enrollment_cycle_id)
                rec.previous_list_ids = cycle.beneficiary_list_ids.filtered(lambda l: l.id != rec.beneficiary_list_id)
            elif rec.disbursement_cycle_id:
                cycle = self.env["g2p.disbursement.cycle"].browse(rec.disbursement_cycle_id)
                rec.previous_list_ids = cycle.beneficiary_list_ids.filtered(lambda l: l.id != rec.beneficiary_list_id)
            else:
                rec.previous_list_ids = BeneficiaryList
            rec.has_previous_versions = bool(rec.previous_list_ids)

    @api.depends("disbursement_cycle_id")
    def _compute_disbursement_cycle_nav(self):
        DisbursementCycle = self.env["g2p.disbursement.cycle"]
        for rec in self:
            if not rec.disbursement_cycle_id:
                rec.prev_disbursement_cycle_id = False
                rec.next_disbursement_cycle_id = False
                rec.is_viewing_current_disbursement_cycle = True
                continue
            cycle = DisbursementCycle.browse(rec.disbursement_cycle_id)
            if not cycle.exists() or not cycle.program_id:
                rec.prev_disbursement_cycle_id = False
                rec.next_disbursement_cycle_id = False
                rec.is_viewing_current_disbursement_cycle = True
                continue
            program_id = cycle.program_id.id
            cycle_number = cycle.cycle_number
            prev = DisbursementCycle.search([
                ("program_id", "=", program_id),
                ("cycle_number", "<", cycle_number),
            ], order="cycle_number desc", limit=1)
            nxt = DisbursementCycle.search([
                ("program_id", "=", program_id),
                ("cycle_number", ">", cycle_number),
            ], order="cycle_number asc", limit=1)
            latest = DisbursementCycle.search([
                ("program_id", "=", program_id),
            ], order="cycle_number desc", limit=1)
            rec.prev_disbursement_cycle_id = prev or False
            rec.next_disbursement_cycle_id = nxt or False
            rec.is_viewing_current_disbursement_cycle = latest.id == rec.disbursement_cycle_id

    @api.depends("enrollment_cycle_id")
    def _compute_enrollment_cycle_nav(self):
        EnrollmentCycle = self.env["g2p.enrollment.cycle"]
        for rec in self:
            if not rec.enrollment_cycle_id:
                rec.prev_enrollment_cycle_id = False
                rec.next_enrollment_cycle_id = False
                rec.is_viewing_current_enrollment_cycle = True
                continue
            cycle = EnrollmentCycle.browse(rec.enrollment_cycle_id)
            if not cycle.exists() or not cycle.program_id:
                rec.prev_enrollment_cycle_id = False
                rec.next_enrollment_cycle_id = False
                rec.is_viewing_current_enrollment_cycle = True
                continue
            program_id = cycle.program_id.id
            cycle_number = cycle.cycle_number
            prev = EnrollmentCycle.search([
                ("program_id", "=", program_id),
                ("cycle_number", "<", cycle_number),
            ], order="cycle_number desc", limit=1)
            nxt = EnrollmentCycle.search([
                ("program_id", "=", program_id),
                ("cycle_number", ">", cycle_number),
            ], order="cycle_number asc", limit=1)
            latest = EnrollmentCycle.search([
                ("program_id", "=", program_id),
            ], order="cycle_number desc", limit=1)
            rec.prev_enrollment_cycle_id = prev or False
            rec.next_enrollment_cycle_id = nxt or False
            rec.is_viewing_current_enrollment_cycle = latest.id == rec.enrollment_cycle_id

    def action_prev_cycle(self):
        self.ensure_one()
        if self.list_stage == "disbursement":
            cycle = self.prev_disbursement_cycle_id
        else:
            cycle = self.prev_enrollment_cycle_id
        if not cycle:
            return
        return cycle.action_open_view()

    def action_next_cycle(self):
        self.ensure_one()
        if self.list_stage == "disbursement":
            cycle = self.next_disbursement_cycle_id
        else:
            cycle = self.next_enrollment_cycle_id
        if not cycle:
            return
        return cycle.action_open_view()

    def action_current_cycle(self):
        self.ensure_one()
        if self.list_stage == "disbursement" and self.disbursement_cycle_id:
            cycle = self.env["g2p.disbursement.cycle"].browse(self.disbursement_cycle_id)
            if cycle.exists() and cycle.program_id:
                latest = self.env["g2p.disbursement.cycle"].search(
                    [("program_id", "=", cycle.program_id.id)],
                    order="cycle_number desc", limit=1
                )
                return latest.action_open_view()
        elif self.enrollment_cycle_id:
            cycle = self.env["g2p.enrollment.cycle"].browse(self.enrollment_cycle_id)
            if cycle.exists() and cycle.program_id:
                latest = self.env["g2p.enrollment.cycle"].search(
                    [("program_id", "=", cycle.program_id.id)],
                    order="cycle_number desc", limit=1
                )
                return latest.action_open_view()

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
    def _compute_eligibility_group_title(self):
        for rec in self:
            rec.eligibility_group_title = 'Eligibility Statistics for %s' % rec.target_registry.capitalize()
    
    @api.depends('target_registry')
    def _compute_entitlement_group_title(self):
        for rec in self:
            rec.entitlement_group_title = 'Entitlement Statistics for %s' % rec.target_registry.capitalize()

    def _build_sql_query(self, odoo_domain, target_registry):
        sql_query=""
        order_by_field="internal_record_id"
        try:
            domain_value = safe_eval(odoo_domain or "[]")
        except Exception as e:
            _logger.error(
                "Error evaluating domain: %s",
                e,
            )
            sql_query = "Invalid search term"
            return sql_query, order_by_field

        target_model_name = G2PTargetModelMapping.get_target_model_name(target_registry)

        if not target_model_name:
            _logger.error(
                "Unknown target_registry '%s'",
                target_registry,
            )
            sql_query = "Unknown target registry type"
            return sql_query, order_by_field

        target_model = self.env[target_model_name]

        try:
            query = target_model._where_calc(domain_value)
        except Exception as e:
            _logger.error(
                "Error calculating where clause for rule: %s", e
            )
            sql_query = "Error calculating query"
            return sql_query, order_by_field

        try:
            _, where_clause, where_clause_params = query.get_sql()
        except Exception as e:
            _logger.error(
                "Error generating SQL from query: %s", e
            )
            sql_query = "Error generating SQL"
            return sql_query, order_by_field

        where_str = ("%s" % where_clause) if where_clause else ""
        # Use the target model's table name in the SQL query.
        query_str = (
            where_str 
        )
        
        # Format the parameters as strings.
        formatted_params = list(
            map(lambda x: "'" + str(x) + "'", where_clause_params)
        )

        try:
            formatted_query = query_str % tuple(formatted_params)
            # formatted_query = formatted_query.replace('"', '\\"')
            sql_query = formatted_query
            _logger.info("Query: %s", sql_query)
        except Exception as e:
            _logger.error(
                "Error formatting query: %s",
                e,
            )
            sql_query = "Error formatting query"
        return sql_query, order_by_field

    @api.model
    def get_beneficiaries(self, wizard_id, page, page_size, odoo_domain):
        wizard = self.sudo().browse(wizard_id)
        api_url = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.staff_portal_api_url')
        sender_id = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.keymanager_sign_application_id')

        if not api_url:
            _logger.error("API URL not set in environment")

        sql_query, order_by_condition = self._build_sql_query(odoo_domain, wizard.target_registry)
        endpoint = f"{api_url}/search_beneficiaries"

        payload = BeneficiarySearchRequest(
            request_header=G2PRequestHeader(
                sender_app_mnemonic=sender_id,
                sender_app_url="",
                request_id="string",
                request_timestamp=datetime.utcnow().isoformat(),
            ),
            request_body=BeneficiarySearchRequestBody(
                pagination_request=G2PPaginationRequest(
                    current_page=page,
                    page_size=page_size,
                    sort_by=order_by_condition or "internal_record_id asc",
                    search_text=sql_query or "",
                ),
                request_payload=BeneficiarySearchRequestPayload(
                    beneficiary_list_id=wizard.beneficiary_list_uuid,
                    target_registry=wizard.target_registry,
                ),
            ),
        )
        payload = payload.model_dump(mode="json")

        jwt_token = self.env['keymanager.provider'].jwt_sign_keymanager(json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True))
        headers = {
            "content-type": "application/json",
            "Signature": jwt_token
        }
        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            response_json = response.json()
        except Exception as e:
            _logger.error("API call failed: %s", e)
            return {
                "response_body": {
                    "pagination_response": {
                        "number_of_items": 0,
                        "number_of_pages": 0,
                    },
                    "response_payload": {
                        "beneficiary_count": 0,
                        "beneficiaries": []
                    }
                }
            }
        return response_json

    @api.depends('target_registry')
    def _compute_summary_lines(self):
        excluded_keys = ['id', 'target_registry']
        for wizard in self:
            wizard.summary_line_ids = [(5, 0, 0)]
            if not wizard.beneficiary_list_uuid:
                continue
            api_url = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.staff_portal_api_url')
            sender_id = self.env['ir.config_parameter'].sudo().get_param('g2p_pbms.keymanager_sign_application_id')

            if not api_url or not sender_id:
                _logger.warning("API_URL or sender_id not set in environment")
                continue
            endpoint = f"{api_url}/summary"

            payload = SummaryRequest(
                request_header=G2PRequestHeader(
                    sender_app_mnemonic=sender_id,
                    sender_app_url="",
                    request_id="string",
                    request_timestamp=datetime.utcnow().isoformat(),
                ),
                request_body=SummaryRequestBody(
                    request_payload=SummaryRequestPayload(
                        beneficiary_list_id=wizard.beneficiary_list_uuid,
                        target_registry=wizard.target_registry,
                    ),
                ),
            )
            payload = payload.model_dump(mode="json")

            try:
                jwt_token = self.env['keymanager.provider'].jwt_sign_keymanager(json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True))
                headers = {
                    "content-type": "application/json",
                    "Signature": jwt_token
                }
                response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
                response.raise_for_status()
                api_response = response.json()
                _logger.debug("API response: %s", api_response)
            except Exception as e:
                _logger.error("API call failed at summary API endpoint %s: %s" % (endpoint, str(e)))
                return

            lines = []
            response_body = api_response.get('response_body', {})
            response_payload = response_body.get('response_payload', {})
            summary = response_payload.get('summary')

            # If summary is None or not a dict, skip processing
            if not summary or not isinstance(summary, dict):
                _logger.warning("Summary data is missing or invalid in API response")
                return

            # Prepare benefit_code_id to mnemonic mapping
            benefit_code_obj = self.env['g2p.benefit.codes'].sudo()
            all_benefit_codes = benefit_code_obj.search([])
            benefit_code_id_to_mnemonic = {str(b.id): b.benefit_mnemonic for b in all_benefit_codes}
            benefit_code_id_to_unit = {str(b.id): b.measurement_unit for b in all_benefit_codes}

            # Flatten all keys from beneficiary_list_summary
            for key, value in summary.get('beneficiary_list_summary', {}).items():
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
                        'summary_type': 'general'
                    }))

            # Flatten all keys from registry_summary
            for key, value in summary.get('registry_summary', {}).items():
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
                        # Format the value with thousands separator if it's a number, otherwise convert to string
                        'value': '{:,}'.format(int(value)) if isinstance(value, (int, float)) else str(value),
                        'summary_type': 'eligibility'
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

            payload = DisbursementEnvelopeRequest(
                request_header=G2PRequestHeader(
                    sender_app_mnemonic=sender_id,
                    sender_app_url="",
                    request_id="string",
                    request_timestamp=datetime.utcnow().isoformat(),
                ),
                request_body=DisbursementEnvelopeRequestBody(
                    request_payload=DisbursementEnvelopeRequestPayload(
                        beneficiary_list_id=wizard.beneficiary_list_uuid,
                    ),
                ),
            )
            payload = payload.model_dump(mode="json")

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
            response_body = api_response.get('response_body', {})
            response_payload = response_body.get('response_payload', {})
            envelope_list = response_payload.get('disbursement_envelopes', [])
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

            payload = DisbursementBatchRequest(
                request_header=G2PRequestHeader(
                    sender_app_mnemonic=sender_id,
                    sender_app_url="",
                    request_id="string",
                    request_timestamp=datetime.utcnow().isoformat(),
                ),
                request_body=DisbursementBatchRequestBody(
                    request_payload=DisbursementBatchRequestPayload(
                        beneficiary_list_id=wizard.beneficiary_list_uuid,
                    ),
                ),
            )
            payload = payload.model_dump(mode="json")

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
            response_body = api_response.get('response_body', {})
            response_payload = response_body.get('response_payload', {})
            batch_list = response_payload.get('disbursement_batches', [])
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
    def _compute_entitlement_summary_html(self):
        for wizard in self:
            _logger.info("=== Entitlement HTML Debug ===")
            _logger.info("beneficiary_list_id: %s", wizard.beneficiary_list_id)

            if not wizard.beneficiary_list_id:
                wizard.entitlement_summary_html = False
                _logger.info("No beneficiary_list_id")
                continue

            # Get the beneficiary list record
            list_rec = self.env["g2p.beneficiary.list"].browse(wizard.beneficiary_list_id)
            _logger.info("list_rec exists: %s", list_rec.exists())
            _logger.info("disbursement_quantity: %s", list_rec.disbursement_quantity if list_rec.exists() else 'N/A')
            _logger.info("list_stage: %s", wizard.list_stage)
            _logger.info("entitlement_process_status: %s", list_rec.entitlement_process_status if list_rec.exists() else 'N/A')

            if not list_rec.exists() or not list_rec.disbursement_quantity:
                wizard.entitlement_summary_html = False
                _logger.info("No disbursement_quantity data")
                continue

            try:
                data = json.loads(list_rec.disbursement_quantity)
                _logger.info("Parsed data: %s (type: %s)", data, type(data).__name__)
                items = []

                if isinstance(data, list):
                    for item in data:
                        code = item.get('benefit_code') or item.get('benefit_mnemonic') or ''
                        qty = item.get('quantity') or item.get('total_disbursement_quantity') or ''
                        unit = item.get('unit') or item.get('measurement_unit') or ''
                        if code:
                            items.append("<li>%s: %s %s</li>" % (code, qty, unit))
                elif isinstance(data, dict):
                    for code, details in data.items():
                        if isinstance(details, dict):
                            qty = details.get('quantity') or details.get('total_disbursement_quantity') or ''
                            unit = details.get('unit') or details.get('measurement_unit') or ''
                        else:
                            qty = details
                            unit = ''
                        items.append("<li>%s: %s %s</li>" % (code, qty, unit))

                if items:
                    html = "<ul style='margin:0;padding-left:18px;'>%s</ul>" % "".join(items)
                    wizard.entitlement_summary_html = html
                    _logger.info("Generated HTML: %s", html)
                else:
                    wizard.entitlement_summary_html = False
                    _logger.info("No items parsed from data")
            except Exception as e:
                _logger.warning("Failed to parse disbursement_quantity: %s", e)
                wizard.entitlement_summary_html = False

    entitlement_summary_html = fields.Html(
        string="Entitlements",
        compute="_compute_entitlement_summary_html",
        sanitize=False,
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

    def write(self, vals):
        """Handle priority_rule_ids explicitly to avoid related One2many refresh issues on TransientModel."""
        rule_commands = vals.pop('priority_rule_ids', None)
        res = super().write(vals)
        if rule_commands is not None:
            for rec in self:
                if rec.disbursement_cycle_m2o_id:
                    rec.disbursement_cycle_m2o_id.write({'priority_rule_ids': rule_commands})
                    rec.disbursement_cycle_m2o_id.invalidate_recordset(['priority_rule_ids'])
            self.invalidate_recordset(['priority_rule_ids'])
        return res

    def action_refresh_data(self):
        """Rebuild wizard from source data (simulates go-back + View)."""
        self.ensure_one()
        if self.beneficiary_list_id:
            bl = self.env["g2p.beneficiary.list"].browse(self.beneficiary_list_id)
            if bl.exists():
                return bl.action_open_summary_wizard()
        if self.disbursement_cycle_m2o_id:
            return self.disbursement_cycle_m2o_id.action_open_view()
        return True

    def action_create_new_version(self):
        """Create a new version/list for the cycle"""
        self.ensure_one()
        if self.enrollment_cycle_id:
            cycle = self.env["g2p.enrollment.cycle"].browse(self.enrollment_cycle_id)
            return cycle.action_create_new_list()
        elif self.disbursement_cycle_id:
            cycle = self.env["g2p.disbursement.cycle"].browse(self.disbursement_cycle_id)
            return cycle.action_create_new_list()
        else:
            raise UserError("No cycle associated with this wizard.")

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
            # payload = {
            #     "header": {
            #         "version": "1.0.0",
            #         "message_id": "string",
            #         "message_ts": "string",
            #         "action": "get_disbursement_envelope_status",
            #         "sender_id": sender_id,
            #         "sender_uri": "",
            #         "receiver_id": "",
            #         "total_count": 0,
            #         "is_msg_encrypted": False,
            #         "meta": "string"
            #     },
            #     "message": self.disbursement_envelope_id,
            # }
            payload = DisbursementEnvelopeStatusRequest(
                request_header=G2PRequestHeader(
                    sender_app_mnemonic=sender_id,
                    sender_app_url="",
                    request_id="string",
                    request_timestamp=datetime.utcnow().isoformat(),
                    instance_id="string"
                ),
                request_body=DisbursementEnvelopeStatusRequestBody(
                    request_payload=self.disbursement_envelope_id
                ),
            )
            payload = payload.model_dump(mode="json")

            jwt_token = self.env['keymanager.provider'].jwt_sign_keymanager(json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True))
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Signature": jwt_token
            }
            try:
                response = requests.post(endpoint, json=payload, timeout=10, headers=headers)
                response.raise_for_status()
                _logger.debug("Disbursement Envelope Status Response: %s", response.json())
                disbursement_envelope_status_response = DisbursementEnvelopeStatusResponse.model_validate(
                    response.json()
                )
            except Exception as e:
                _logger.error("API call failed: %s", e)
                raise UserError("Failed to fetch disbursement envelope status: %s" % e) from e

            message = (
                disbursement_envelope_status_response.response_body.response_payload
                if disbursement_envelope_status_response.response_body
                else None
            )

            if not message:
                raise UserError("No disbursement envelope status returned.")

            summary_vals = {
                "wizard_id": self.wizard_id.id if self.wizard_id else False,
                "disbursement_envelope_id": message.disbursement_envelope_id,
                "benefit_code_id": self.benefit_code_id,
                "benefit_code_mnemonic": message.benefit_code_mnemonic,
                "benefit_type": message.benefit_type,
                "measurement_unit": message.measurement_unit,
                "number_of_beneficiaries_received": message.number_of_beneficiaries_received,
                "number_of_beneficiaries_declared": message.number_of_beneficiaries_declared,
                "number_of_disbursements_declared": message.number_of_disbursements_declared,
                "number_of_disbursements_received": message.number_of_disbursements_received,
                "total_disbursement_quantity_declared": (
                    "{:,}".format(int(message.total_disbursement_quantity_declared))
                    + (" " + str(message.measurement_unit) if message.measurement_unit else "")
                    if message.total_disbursement_quantity_declared is not None else ""
                ),
                "total_disbursement_quantity_received": (
                    "{:,}".format(int(message.total_disbursement_quantity_received))
                    + (" " + str(message.measurement_unit) if message.measurement_unit else "")
                    if message.total_disbursement_quantity_received is not None else ""
                ),
                "funds_available_with_bank": message.funds_available_with_bank.value if message.funds_available_with_bank else None,
                "funds_available_latest_timestamp": self.odoo_datetime_format(message.funds_available_latest_timestamp) if message.funds_available_latest_timestamp else None,
                "funds_available_latest_error_code": message.funds_available_latest_error_code,
                "funds_available_attempts": message.funds_available_attempts,
                "funds_blocked_with_bank": message.funds_blocked_with_bank.value if message.funds_blocked_with_bank else None,
                "funds_blocked_latest_timestamp": self.odoo_datetime_format(message.funds_blocked_latest_timestamp) if message.funds_blocked_latest_timestamp else None,
                "funds_blocked_latest_error_code": message.funds_blocked_latest_error_code,
                "funds_blocked_attempts": message.funds_blocked_attempts,
                "funds_blocked_reference_number": message.funds_blocked_reference_number,
                "number_of_disbursements_shipped": message.number_of_disbursements_shipped,
                "number_of_disbursements_reconciled": message.number_of_disbursements_reconciled,
                "number_of_disbursements_reversed": message.number_of_disbursements_reversed,
                "no_of_warehouses_allocated": message.no_of_warehouses_allocated,
                "no_of_warehouses_notified": message.no_of_warehouses_notified,
                "no_of_agencies_allocated": message.no_of_agencies_allocated,
                "no_of_agencies_notified": message.no_of_agencies_notified,
                "no_of_beneficiaries_notified": message.no_of_beneficiaries_notified,
                "no_of_pods_received": message.no_of_pods_received,
            }

            geo_lines = []
            disbursement_batch_control_geos = message.disbursement_batch_control_geos
            if disbursement_batch_control_geos:
                for geo in disbursement_batch_control_geos:
                    geo_lines.append((0, 0, {
                        "disbursement_batch_control_geo_id": geo.disbursement_batch_control_geo_id,
                        "disbursement_cycle_id": geo.disbursement_cycle_id,
                        "disbursement_envelope_id": geo.disbursement_envelope_id,
                        "disbursement_batch_control_id": geo.disbursement_batch_control_id,
                        "administrative_zone_id_large": geo.administrative_zone_id_large,
                        "administrative_zone_mnemonic_large": geo.administrative_zone_mnemonic_large,
                        "administrative_zone_id_small": geo.administrative_zone_id_small,
                        "administrative_zone_mnemonic_small": geo.administrative_zone_mnemonic_small,
                        "no_of_beneficiaries": geo.no_of_beneficiaries,
                        "total_quantity": geo.total_quantity,
                        "warehouse_id": geo.warehouse_id,
                        "warehouse_mnemonic": geo.warehouse_mnemonic,
                        "warehouse_additional_attributes": geo.warehouse_additional_attributes,
                        "agency_id": geo.agency_id,
                        "agency_mnemonic": geo.agency_mnemonic,
                        "agency_additional_attributes": geo.agency_additional_attributes,
                        "warehouse_notification_status": geo.warehouse_notification_status,
                        "agency_notification_status": geo.agency_notification_status,
                    }))
            if geo_lines:
                summary_vals["disbursement_envelope_summary_geo_ids"] = geo_lines

            summary = self.env["g2p.disbursement.envelope.summary.wizard"].create(summary_vals)

            return self.env.ref("g2p_pbms.action_generate_disbursement_envelope_summary").report_action(summary)


        except requests.exceptions.RequestException as e:
            raise UserError("Failed to fetch disbursement envelope status: %s" % e) from e
        except Exception as e:
            raise UserError("An error occurred: %s" % e) from e
        
    def odoo_datetime_format(self, dt_value):
        if not dt_value:
            return None

        if isinstance(dt_value, datetime):
            return dt_value.strftime('%Y-%m-%d %H:%M:%S')

        try:
            dt = datetime.fromisoformat(dt_value)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return dt_value

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
            # payload = {
            #     "header": {
            #         "version": "1.0.0",
            #         "message_id": "string",
            #         "message_ts": "string",
            #         "action": "get_disbursement_batch_control",
            #         "sender_id": sender_id,
            #         "sender_uri": "",
            #         "receiver_id": "",
            #         "total_count": 0,
            #         "is_msg_encrypted": False,
            #         "meta": "string"
            #     },
            #     "message": self.batch_id,
            # }
            payload = DisbursementBatchControlRequest(
                request_header=G2PRequestHeader(
                    sender_app_mnemonic=sender_id,
                    sender_app_url="",
                    request_id="string",
                    request_timestamp=datetime.utcnow().isoformat(),
                    instance_id="string"
                ),
                request_body=DisbursementBatchControlRequestBody(
                    request_payload=self.batch_id
                ),
            )
            payload = payload.model_dump(mode="json")

            jwt_token = self.env['keymanager.provider'].jwt_sign_keymanager(json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True))
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Signature": jwt_token
            }
            try:
                response = requests.post(endpoint, json=payload, timeout=10, headers=headers)
                response.raise_for_status()
                _logger.debug("Disbursement Batch Status Response: %s", response.json())
                disbursement_batch_control_response = DisbursementBatchControlResponse.model_validate(
                    response.json()
                )
            except Exception as e:
                _logger.error("API call failed: %s", e)
                raise UserError("Failed to fetch disbursement batch status: %s" % e) from e

            message = (
                disbursement_batch_control_response.response_body.response_payload
                if disbursement_batch_control_response.response_body
                else None
            )

            if not message:
                raise UserError("No disbursement batch control status returned.")

            summary_vals = {
                "wizard_id": self.wizard_id.id if self.wizard_id else False,
                "disbursement_batch_control_id": message.disbursement_batch_control_id,
                "disbursement_cycle_id": message.disbursement_cycle_id,
                "disbursement_cycle_code_mnemonic": message.disbursement_cycle_code_mnemonic,
                "disbursement_envelope_id": message.disbursement_envelope_id,
                "benefit_code_id": message.benefit_code_id,
                "benefit_code_mnemonic": message.benefit_code_mnemonic,
                "benefit_type": message.benefit_type,
                "measurement_unit": message.measurement_unit,
                "fa_resolution_status": message.fa_resolution_status,
                "fa_resolution_timestamp": self.odoo_datetime_format(message.fa_resolution_timestamp) if message.fa_resolution_timestamp else None,
                "fa_resolution_latest_error_code": message.fa_resolution_latest_error_code,
                "fa_resolution_attempts": message.fa_resolution_attempts,
                "sponsor_bank_dispatch_status": message.sponsor_bank_dispatch_status,
                "sponsor_bank_dispatch_timestamp": self.odoo_datetime_format(message.sponsor_bank_dispatch_timestamp) if message.sponsor_bank_dispatch_timestamp else None,
                "sponsor_bank_dispatch_latest_error_code": message.sponsor_bank_dispatch_latest_error_code,
                "sponsor_bank_dispatch_attempts": message.sponsor_bank_dispatch_attempts,
                "geo_resolution_status": message.geo_resolution_status,
                "geo_resolution_timestamp": self.odoo_datetime_format(message.geo_resolution_timestamp) if message.geo_resolution_timestamp else None,
                "geo_resolution_latest_error_code": message.geo_resolution_latest_error_code,
                "geo_resolution_attempts": message.geo_resolution_attempts,
                "warehouse_allocation_status": message.warehouse_allocation_status,
                "warehouse_allocation_timestamp": self.odoo_datetime_format(message.warehouse_allocation_timestamp) if message.warehouse_allocation_timestamp else None,
                "warehouse_allocation_latest_error_code": message.warehouse_allocation_latest_error_code,
                "warehouse_allocation_attempts": message.warehouse_allocation_attempts,
                "agency_allocation_status": message.agency_allocation_status,
                "agency_allocation_timestamp": self.odoo_datetime_format(message.agency_allocation_timestamp) if message.agency_allocation_timestamp else None,
                "agency_allocation_latest_error_code": message.agency_allocation_latest_error_code,
                "agency_allocation_attempts": message.agency_allocation_attempts,
            }

            geo_lines = []
            disbursement_batch_control_geos = message.disbursement_batch_control_geos
            if disbursement_batch_control_geos:
                for geo in disbursement_batch_control_geos:
                    geo_lines.append((0, 0, {
                        "disbursement_batch_control_geo_id": geo.disbursement_batch_control_geo_id,
                        "disbursement_cycle_id": geo.disbursement_cycle_id,
                        "disbursement_envelope_id": geo.disbursement_envelope_id,
                        "disbursement_batch_control_id": geo.disbursement_batch_control_id,
                        "administrative_zone_id_large": geo.administrative_zone_id_large,
                        "administrative_zone_mnemonic_large": geo.administrative_zone_mnemonic_large,
                        "administrative_zone_id_small": geo.administrative_zone_id_small,
                        "administrative_zone_mnemonic_small": geo.administrative_zone_mnemonic_small,
                        "no_of_beneficiaries": geo.no_of_beneficiaries,
                        "total_quantity": geo.total_quantity,
                        "warehouse_id": geo.warehouse_id,
                        "warehouse_mnemonic": geo.warehouse_mnemonic,
                        "warehouse_additional_attributes": geo.warehouse_additional_attributes,
                        "agency_id": geo.agency_id,
                        "agency_mnemonic": geo.agency_mnemonic,
                        "agency_additional_attributes": geo.agency_additional_attributes,
                        "warehouse_notification_status": geo.warehouse_notification_status,
                        "agency_notification_status": geo.agency_notification_status,
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
    def odoo_datetime_format(self, dt_value):
        if not dt_value:
            return None

        if isinstance(dt_value, datetime):
            return dt_value.strftime('%Y-%m-%d %H:%M:%S')

        try:
            dt = datetime.fromisoformat(dt_value)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return dt_value