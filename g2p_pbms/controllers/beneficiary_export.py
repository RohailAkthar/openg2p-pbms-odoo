import io
import csv
import json
import math
import logging
import requests
import time
from odoo import http, api
from odoo.http import request

_logger = logging.getLogger(__name__)

PAGE_SIZE = 1000


class G2PBeneficiaryExportController(http.Controller):

    # Ordered list of (raw_field, friendly_header)
    # Fields not listed here will be excluded from the export
    EXPORT_COLUMNS = [
        ("name", "Beneficiary Name"),
        ("benf_zan_id", "Zanzibar ID"),
        ("account_num", "Account Number"),
        ("nominee_name", "Next of Kin Name"),
        ("amount", "Amount"),
        ("street", "Shehia"),
        ("district_name", "District"),
        ("region_name", "Area"),
        ("gender", "Gender"),
    ]

    @http.route(
        "/g2p_pbms/export/beneficiaries/<int:wizard_id>",
        type="http",
        auth="user"
    )
    def export_beneficiaries(self, wizard_id, **kw):
        # Initial check with user permissions
        wizard = request.env["g2p.bgtask.summary.wizard"].browse(wizard_id)
        if not wizard.exists():
            return request.not_found()

        registry = request.env.registry
        uid = request.uid
        context = dict(request.env.context)
        
        def generate_csv_data():
            with registry.cursor() as new_cr:
                new_env = api.Environment(new_cr, uid, context)
                # sudo() is needed for cross-model API access and system-level Bridge API calls
                wizard_new = new_env["g2p.bgtask.summary.wizard"].sudo().browse(wizard_id)
                
                output = io.StringIO()
                writer = csv.writer(output)

                # Yield BOM + Headers immediately
                output.write('\ufeff')
                friendly_headers = [header for _, header in self.EXPORT_COLUMNS]
                writer.writerow(friendly_headers)
                yield output.getvalue()
                output.truncate(0)
                output.seek(0)

                page = 1
                while True:
                    # ✅ Safety Check: Ensure wizard still exists mid-stream
                    if not wizard_new.exists():
                        _logger.error("Wizard %s deleted during export", wizard_id)
                        writer.writerow(["ERROR", "Task record was deleted during export."])
                        yield output.getvalue()
                        break

                    records = []
                    # ✅ Retry Logic: 3 attempts per batch
                    for attempt in range(3):
                        try:
                            _logger.info("Fetching batch %s (Attempt %s) for wizard %s", page, attempt + 1, wizard_id)
                            res = wizard_new.get_beneficiaries(wizard_new.id, page, PAGE_SIZE, None)
                            records = res.get("message", {}).get("beneficiaries") or []
                            break
                        except Exception as e:
                            _logger.warning("Batch %s attempt %s failed: %s", page, attempt + 1, e)
                            new_cr.rollback()
                            if attempt == 2:
                                # ✅ Final Failure: Write error row to CSV
                                _logger.error("Batch %s failed permanently after 3 attempts", page)
                                writer.writerow(["ERROR", f"Failed to fetch batch {page} after 3 attempts: {str(e)}"])
                                yield output.getvalue()
                                return # Kill the stream
                            time.sleep(1) # Backoff before retry

                    if not records:
                        _logger.info("Export complete at page %s", page)
                        break

                    # Retrieve program's entitlement rules to determine quantity and multiplier
                    rules = wizard_new.program_id.entitlement_rule_ids.filtered(
                        lambda r: r.target_registry == wizard_new.target_registry
                    )
                    quantity = rules[0].quantity if rules else 0.0
                    multiplier_field = rules[0].multiplier if (rules and rules[0].multiplier) else False

                    # Fetch multiplier values in batch
                    multiplier_values = {}
                    if multiplier_field:
                        partner_ids = [r.get("id") for r in records if r.get("id")]
                        if partner_ids:
                            partners = new_env["res.partner"].sudo().browse(partner_ids)
                            for p in partners:
                                val = getattr(p, multiplier_field, 1.0) if multiplier_field in p._fields else 1.0
                                try:
                                    multiplier_values[p.id] = float(val) if val is not None else 1.0
                                except (ValueError, TypeError):
                                    multiplier_values[p.id] = 1.0

                    for row in records:
                        nominee_parts = [
                            str(row.get(f)).strip()
                            for f in ("nominee_first_name", "nominee_middle_name", "nominee_last_name")
                            if row.get(f) and str(row.get(f)).strip()
                        ]
                        row["nominee_name"] = " ".join(nominee_parts) if nominee_parts else ""

                        # Calculate amount dynamically
                        partner_id = row.get("id")
                        mult_val = multiplier_values.get(partner_id, 1.0)
                        row["amount"] = quantity * mult_val

                        csv_row = []
                        for field, _ in self.EXPORT_COLUMNS:
                            val = row.get(field)
                            if isinstance(val, (dict, list)):
                                val = json.dumps(val)
                            csv_row.append(str(val if val is not None else ""))
                        writer.writerow(csv_row)

                    data = output.getvalue()
                    if data:
                        yield data
                    
                    output.truncate(0)
                    output.seek(0)
                    _logger.info("Exported batch %s, records=%s", page, len(records))
                    
                    if len(records) < PAGE_SIZE:
                        break
                    page += 1

        filename = "beneficiaries_%s.csv" % wizard.id
        return request.make_response(
            generate_csv_data(),
            headers=[
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Content-Disposition", 'attachment; filename="%s"' % filename),
                ("Cache-Control", "no-cache")
            ]
        )
