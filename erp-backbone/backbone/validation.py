"""Field validation response contract shared by the Django endpoints."""

from contextlib import contextmanager

MESSAGES = {
    "ambiguous_time": "Enter the UTC offset for this repeated local time.",
    "invalid_local_time": "Choose a time that exists in this site timezone.",
    "invalid_shift_end": "Choose an end time after the start and no later than now, within 31 days.",
    "required": "This field is required.",
    "invalid": "Enter a valid value.",
    "invalid_number": "Enter a number with the allowed digits and decimal places.",
    "invalid_date": "Enter a valid date.",
    "date_order": "The end date must be on or after the start date.",
    "employment_date": "Choose a date within the employment period.",
    "line_direction": "Enter a positive amount on either debit or credit.",
    "min_value": "Enter a value greater than zero.",
    "max_length": "The value is too long.",
    "duplicate": "This value is already in use.",
    "invalid_image": "Choose an image with the supported format and size.",
    "invalid_choice": "Select a valid option.",
}

# Some existing field errors have an operation-specific code. Keep that code
# for clients and return the writable field alongside it.
FIELD_ALIASES = {
    "hr_employee_number_exists": ("number", "duplicate"),
    "hr_reference_code_exists": ("code", "duplicate"),
    "hr_user_already_linked": ("user_id", "duplicate"),
    "duplicate_supplier_code": ("code", "duplicate"),
    "sales_customer_code_exists": ("code", "duplicate"),
    "inventory_code_exists": ("code", "duplicate"),
    "duplicate_name": ("name", "duplicate"),
    "ap_duplicate_invoice": ("supplier_number", "duplicate"),
    "invalid_supplier_code": ("code", "invalid"),
    "invalid_company_logo": ("logo", "invalid_image"),
    "site_inactive": ("site_id", "invalid_choice"),
}
NON_FIELD_ERRORS = {"invalid_json", "invalid_idempotency_key", "invalid_revision", "invalid_action"}


class Problem(Exception):
    def __init__(self, code, status=400, *, fields=None, field=None, reason="invalid"):
        super().__init__(code)
        self.code = code
        self.status = status
        self.fields = fields or ({field: [reason]} if field else {})

    def field_codes(self):
        if self.fields:
            return self.fields
        if self.code in FIELD_ALIASES:
            field, reason = FIELD_ALIASES[self.code]
            return {field: [reason]}
        if self.status == 400 and self.code.startswith("invalid_") and self.code not in NON_FIELD_ERRORS:
            field = self.code.removeprefix("invalid_")
            return {field: ["invalid_date" if field == "date" else "invalid"]}
        return {}

    def response(self):
        result = {"error": self.code}
        codes = self.field_codes()
        if codes:
            result["field_errors"] = {
                field: [MESSAGES[code] for code in reasons] for field, reasons in codes.items()
            }
            result["field_error_codes"] = codes
        return result


class Validation:
    """Collect independent input errors before writing anything."""

    def __init__(self):
        self.fields = {}
        self.first = None

    def read(self, field, parse, *args, **kwargs):
        try:
            return parse(*args, **kwargs)
        except Problem as error:
            if error.status != 400:
                raise
            reasons = next(iter(error.field_codes().values()), ["invalid"])
            self.add(field, error.code, reasons)
            return None

    def add(self, field, legacy_code, reasons):
        self.first = self.first or legacy_code
        self.fields.setdefault(field, []).extend(reasons)

    def finish(self):
        if self.fields:
            raise Problem(self.first, fields=self.fields)


@contextmanager
def field_path(prefix):
    """Keep the array position when a nested value fails validation."""
    try:
        yield
    except Problem as error:
        codes = error.field_codes()
        if error.status == 400 and codes:
            fields = {f"{prefix}.{name}": reasons for name, reasons in codes.items()}
            raise Problem(error.code, error.status, fields=fields) from None
        raise
