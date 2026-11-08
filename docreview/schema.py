"""Strict, nullable extraction schema; monetary values never use floats."""
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
import re

FIELDS = ('invoice_id', 'vendor', 'invoice_date', 'currency', 'subtotal', 'tax', 'total')
MONEY = ('subtotal', 'tax', 'total')
SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': list(FIELDS),
    'properties': {key: {'type': ['string', 'null'], 'minLength': 1, 'maxLength': 200}
                   for key in FIELDS},
}
for key in MONEY:
    SCHEMA['properties'][key]['pattern'] = r'^(0|[1-9][0-9]{0,9})\.[0-9]{2}$'
SCHEMA['properties']['currency']['enum'] = ['USD', 'EUR', 'GBP', None]
SCHEMA['properties']['invoice_date']['format'] = 'date'
SCHEMA['properties']['invoice_date']['pattern'] = r'^\d{4}-\d{2}-\d{2}$'
for key in ('invoice_id', 'vendor'):
    SCHEMA['properties'][key]['pattern'] = r'\S'


@dataclass(frozen=True)
class Invoice:
    invoice_id: str | None
    vendor: str | None
    invoice_date: date | None
    currency: str | None
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, dict) or set(raw) != set(FIELDS):
            raise ValueError('Invoice must contain exactly: ' + ', '.join(FIELDS))
        parsed = {}
        for key, value in raw.items():
            if value is None:
                parsed[key] = None
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f'{key}: expected a nonempty string (maximum 200 characters) or null')
            if key in MONEY:
                if not re.fullmatch(r'(0|[1-9][0-9]{0,9})\.[0-9]{2}', value):
                    raise ValueError(f'{key}: expected nonnegative decimal string with two places')
                parsed[key] = Decimal(value)
            elif key == 'invoice_date':
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    raise ValueError('invoice_date: expected YYYY-MM-DD')
                parsed[key] = date.fromisoformat(value)
            elif key == 'currency':
                if value not in ('USD', 'EUR', 'GBP'):
                    raise ValueError('currency: supported values are USD, EUR, GBP')
                parsed[key] = value
            else:
                parsed[key] = value
        return cls(**parsed)

    def json(self):
        return {key: str(value) if value is not None else None
                for key, value in asdict(self).items()}

    def reasons(self):
        reasons = [f'missing:{key}' for key in FIELDS if getattr(self, key) is None]
        if all(getattr(self, key) is not None for key in MONEY):
            if self.subtotal + self.tax != self.total:
                reasons.append('amount_mismatch:subtotal + tax != total')
        return reasons
