import io
import csv
import json
from odoo import http
from odoo.http import request


class G2PBeneficiaryExportController(http.Controller):

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
            # take keys from first record
            fieldnames = list(all_rows[0].keys())
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()

            for row in all_rows:
                writer.writerow({
                    k: json.dumps(v) if isinstance(v, (dict, list)) else v
                    for k, v in row.items()
                })

        filename = "beneficiaries_%s.csv" % wizard.id

        return request.make_response(
            output.getvalue(),
            headers=[
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Content-Disposition", 'attachment; filename="%s"' % filename)
            ]
        )
