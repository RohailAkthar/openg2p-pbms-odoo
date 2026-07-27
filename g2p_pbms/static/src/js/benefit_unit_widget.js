/** @odoo-module **/
import { CharField } from "@web/views/fields/char/char_field";
import { registry } from "@web/core/registry";
import { onWillUpdateProps, useState } from "@odoo/owl";


export class BenefitUnitWidget extends CharField {
    static template = "g2p_pbms.BenefitUnitWidget";

    setup() {
        super.setup();
        console.log("THIS:", this)
        this.state = useState({
            record_data: this.props.record.data,
        });

        this.currencyOptions = [
            { value: "NGN", label: "NGN" },
            { value: "USD", label: "USD" },
            { value: "EUR", label: "EUR" },
            { value: "GBP", label: "GBP" },
            { value: "JPY", label: "JPY" },
            { value: "INR", label: "INR" },
            { value: "AUD", label: "AUD" },
            { value: "CAD", label: "CAD" },
            { value: "CHF", label: "CHF" },
            { value: "CNY", label: "CNY" },
            { value: "SEK", label: "SEK" },
            { value: "NZD", label: "NZD" },
            { value: "MXN", label: "MXN" },
            { value: "SGD", label: "SGD" },
            { value: "HKD", label: "HKD" },
            { value: "NOK", label: "NOK" },
            { value: "KRW", label: "KRW" },
            { value: "TRY", label: "TRY" },
            { value: "RUB", label: "RUB" },
            { value: "ZAR", label: "ZAR" },
            { value: "AED", label: "AED" },
            { value: "BRL", label: "BRL" },
            { value: "PLN", label: "PLN" },
            { value: "THB", label: "THB" },
            { value: "MYR", label: "MYR" },
            { value: "PHP", label: "PHP" },
            { value: "IDR", label: "IDR" },
            { value: "CZK", label: "CZK" },
            { value: "HUF", label: "HUF" },
            { value: "ILS", label: "ILS" },
            { value: "CLP", label: "CLP" },
            { value: "COP", label: "COP" },
            { value: "PEN", label: "PEN" },
            { value: "DOP", label: "DOP" },
            { value: "VND", label: "VND" },
            { value: "PKR", label: "PKR" },
            { value: "TWD", label: "TWD" },
            { value: "ZMW", label: "ZMW" },
            // Add more ISO codes as needed.
        ];
    }

}

registry.category("fields").add("g2p_pbms.benefit_unit_widget", {
    component: BenefitUnitWidget,
});
