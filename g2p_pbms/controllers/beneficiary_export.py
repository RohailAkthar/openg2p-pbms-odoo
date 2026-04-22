import io
import csv
import json
from odoo import http
from odoo.http import request
import requests


PAGE_SIZE = 1000


class G2PBeneficiaryExportController(http.Controller):

    # Ordered list of (raw_field, friendly_header)
    # Fields not listed here will be excluded from the export
    EXPORT_COLUMNS = [
        ("name", "Name"),
        ("gender", "Gender"),
        ("birthdate", "Date of Birth"),
        ("region_name", "Region"),
        ("district_name", "District"),
        ("benf_zan_id", "ZAN ID"),
        ("street", "Address"),
        ("phone", "Phone"),
        ("benf_post_code", "Post Code"),
        ("disability", "Disability"),
        ("is_receiving_allowance", "Receiving Allowance"),
        ("has_health_insurance", "Health Insurance"),
        ("payment_mode", "Payment Method"),
        ("bank_name", "Bank Name"),
        ("account_num", "Account Number"),
        ("account_name", "Account Name"),
        ("mobile_wallet", "Mobile Wallet"),
        ("other_pension", "Other Pension"),
        ("scheme_name", "Scheme Name"),
        # Nominee fields — first/last name merged into "Nominee Name"
        ("nominee_name", "Nominee Name"),
        ("nominee_gender", "Nominee Gender"),
        ("nominee_zanid", "Nominee ZAN ID"),
        ("nominee_mobile", "Nominee Mobile"),
        ("nominee_rel_benf", "Nominee Relationship"),
        ("nominee_region", "Nominee Region"),
        ("nominee_district", "Nominee District"),
        ("nominee_shehia", "Nominee Shehia"),
        ("nominee_house_street", "Nominee Address"),
        ("nominee_post_code", "Nominee Post Code"),
    ]

    @http.route(
        "/g2p_pbms/export/beneficiaries/<int:wizard_id>",
        type="http",
        auth="user"
    )
    def export_beneficiaries(self, wizard_id, **kw):
        import math

        wizard = request.env["g2p.bgtask.summary.wizard"].sudo().browse(wizard_id)
        if not wizard.exists():
            return request.not_found()

        # ✅ 1. Fetch live count from API /summary endpoint
        api_url = request.env['ir.config_parameter'].sudo().get_param('g2p_pbms.staff_portal_api_url')
        sender_id = request.env['ir.config_parameter'].sudo().get_param('g2p_pbms.keymanager_sign_application_id')
        list_uuid = wizard.beneficiary_list_uuid
        
        total_count = 0
        if api_url and list_uuid:
            try:
                summary_endpoint = f"{api_url}/summary"
                payload = {
                    "signature": "string",
                    "header": {
                        "version": "1.0.0",
                        "message_id": "string",
                        "message_ts": "string",
                        "action": "summary",
                        "sender_id": sender_id,
                        "sender_uri": "",
                        "receiver_id": "",
                        "total_count": 0,
                        "is_msg_encrypted": False,
                        "meta": "string"
                    },
                    "message": {
                        "beneficiary_list_id": list_uuid,
                        "target_registry": wizard.target_registry
                    }
                }
                # Use exact separators for JWT signature consistency
                payload_json = json.dumps(payload, indent=None, separators=(",", ":"), sort_keys=True)
                jwt_token = request.env['keymanager.provider'].sudo().jwt_sign_keymanager(payload_json)
                
                res = requests.post(
                    summary_endpoint, 
                    json=payload, 
                    headers={"Signature": jwt_token, "Content-Type": "application/json"}, 
                    timeout=10
                )
                res.raise_for_status()
                summary_data = res.json()
                
                msg = summary_data.get("message", {})
                total_count = msg.get("beneficiary_list_summary", {}).get("number_of_registrants") or 0
                _logger.info("API Summary reported %s beneficiaries for list %s", total_count, list_uuid)
            except Exception as e:
                _logger.error("Failed to fetch live count from API: %s", e)
        
        # Fallback to Odoo record if API fails or returns 0
        if not total_count:
            beneficiary_list = request.env["g2p.beneficiary.list"].sudo().search([('beneficiary_list_id', '=', list_uuid)], limit=1)
            total_count = beneficiary_list.number_of_registrants or 0
            _logger.info("Falling back to Odoo record count: %s", total_count)

        num_pages = math.ceil(total_count / PAGE_SIZE) if total_count > 0 else 0

        def generate_csv_data():
            output = io.StringIO()
            writer = csv.writer(output)

            # ✅ 1. Yield BOM + Headers immediately to initiate download
            output.write('\ufeff')
            friendly_headers = [header for _, header in self.EXPORT_COLUMNS]
            writer.writerow(friendly_headers)
            yield output.getvalue()
            output.truncate(0)
            output.seek(0)

            # ✅ 2. Loop through pages based on total count
            for page in range(1, num_pages + 1):
                try:
                    res = wizard.get_beneficiaries(
                        wizard.id,
                        page,
                        PAGE_SIZE,
                        None # No filters
                    )
                    records = res.get("message", {}).get("beneficiaries") or []
                except Exception as e:
                    _logger.error("Export batch %s failed: %s", page, e, exc_info=True)
                    request.env.cr.rollback()
                    continue

                # ✅ 3. Safety break for count drift
                if not records:
                    break

                for row in records:
                    # ✅ 4. Nominee merge logic with stripping
                    nominee_parts = [
                        str(row.get(f)).strip()
                        for f in ("nominee_first_name", "nominee_middle_name", "nominee_last_name")
                        if row.get(f) and str(row.get(f)).strip()
                    ]
                    row["nominee_name"] = " ".join(nominee_parts) if nominee_parts else ""

                    csv_row = []
                    for field, _ in self.EXPORT_COLUMNS:
                        val = row.get(field)
                        if isinstance(val, (dict, list)):
                            val = json.dumps(val)
                        # ✅ 5. Safe string casting for all columns
                        csv_row.append(str(val if val is not None else ""))
                    writer.writerow(csv_row)

                # ✅ 6. Yield batch only if data exists
                data = output.getvalue()
                if data:
                    yield data
                
                output.truncate(0)
                output.seek(0)

                # ✅ 7. Maintain healthy DB cursor
                request.env.cr.commit()
                _logger.info("Exported batch %s/%s, records=%s", page, num_pages, len(records))

        filename = "beneficiaries_%s.csv" % wizard.id
        return request.make_response(
            generate_csv_data(),
            headers=[
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Content-Disposition", 'attachment; filename="%s"' % filename),
                ("Cache-Control", "no-cache")
            ]
        )
