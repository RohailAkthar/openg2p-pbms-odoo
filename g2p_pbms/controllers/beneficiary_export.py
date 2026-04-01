import io
import csv
import json
from odoo import http
from odoo.http import request


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

        wizard = request.env["g2p.bgtask.summary.wizard"].sudo().browse(wizard_id)

        if not wizard.exists():
            return request.not_found()

        page = 1
        page_size = 500
        all_rows = []

        # you clearly said : NO filter
        odoo_domain = None

        while True:
            res = wizard.get_beneficiaries(
                wizard.id,
                page,
                page_size,
                odoo_domain
            )

            message = res.get("message", {})
            beneficiaries = message.get("beneficiaries", [])

            if not beneficiaries:
                break

            all_rows.extend(beneficiaries)

            if len(beneficiaries) < page_size:
                break

            page += 1

        output = io.StringIO()

        if not all_rows:
            writer = csv.writer(output)
            writer.writerow(["No data"])
        else:
            # Build friendly headers
            friendly_headers = [header for _, header in self.EXPORT_COLUMNS]
            writer = csv.writer(output)
            writer.writerow(friendly_headers)

            for row in all_rows:
                # Merge nominee first + last name into single "Nominee Name"
                nominee_parts = []
                for name_field in ("nominee_first_name", "nominee_middle_name", "nominee_last_name"):
                    val = row.get(name_field)
                    if val and str(val).strip():
                        nominee_parts.append(str(val).strip())
                row["nominee_name"] = " ".join(nominee_parts) if nominee_parts else None

                csv_row = []
                for field, _ in self.EXPORT_COLUMNS:
                    val = row.get(field)
                    if isinstance(val, (dict, list)):
                        val = json.dumps(val)
                    csv_row.append(val if val is not None else "")
                writer.writerow(csv_row)

        filename = "beneficiaries_%s.csv" % wizard.id

        return request.make_response(
            output.getvalue(),
            headers=[
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Content-Disposition", 'attachment; filename="%s"' % filename)
            ]
        )
