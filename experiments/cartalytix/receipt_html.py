"""Parse observed Czech printed receipt lines, without silently dropping unknown lines."""

import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

AMOUNT = r"[+-]?(?:\d{1,3}(?: \d{3})+|\d+)[.,]\d{2}"
PRODUCT = re.compile(r"^(?P<name>.+?)\s+(?P<amount>" + AMOUNT + r")\s+(?P<tax>[A-Z])$")
QUANTITY = re.compile(
    r"^\s*(?:[A-Z]\s+)?(?P<quantity>[+-]?\d+(?:[.,]\d+)?)\s*(?P<unit>kg|g|ks|l|m)?\s*[*x×]\s*(?P<price>[+-]?\d[\d ]*(?:[.,]\d*)?)",
    re.IGNORECASE,
)
DISCOUNT = re.compile(
    r"^(?P<label>.*?)\s*(?P<amount>-\s*(?:\d{1,3}(?: \d{3})+|\d+)[.,]\d{2})$"
)
NET_AMOUNT = re.compile(r"^.*?\s+(?P<amount>" + AMOUNT + r")$")
TARE = re.compile(r"^PT:\s*(?P<quantity>\d+(?:[.,]\d+)?)\s*kg$", re.IGNORECASE)
ROUNDING = re.compile(r"^zaokrouhleno\s+(?P<amount>" + AMOUNT + r")$", re.IGNORECASE)


def printed_number(value):
    text = value.replace(" ", "").replace(",", ".")
    return text + "00" if text.endswith(".") else text


def product_fields(text, raw_text, attrs):
    # Column spacing and the explicit description disambiguate names ending in digits.
    description = " ".join((attrs.get("data-art-description") or "").split())
    if description and text.startswith(description):
        price_column = text[len(description) :].strip()
        match = re.fullmatch(
            r"(?P<amount>" + AMOUNT + r")\s+(?P<tax>[A-Z])", price_column
        )
        if match:
            return {"name": description, **match.groupdict()}
    columns = re.split(r"\s{2,}", raw_text.strip())
    if len(columns) >= 2:
        match = re.fullmatch(
            r"(?P<amount>" + AMOUNT + r")\s+(?P<tax>[A-Z])", columns[-1]
        )
        if match:
            return {"name": " ".join(columns[:-1]).strip(), **match.groupdict()}
    match = PRODUCT.match(text)
    return match.groupdict() if match else None


class Lines(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.lines = []
        self.currency = None
        self.rounding = None

    def handle_starttag(self, tag, attrs):
        if tag == "span":
            values = dict(attrs)
            self.stack.append({"attrs": values, "text": ""})
            if values.get("data-currency"):
                self.currency = values["data-currency"]

    def handle_data(self, data):
        for row in self.stack:
            row["text"] += data

    def handle_endtag(self, tag):
        if tag == "span" and self.stack:
            row = self.stack.pop()
            attrs = row["attrs"]
            rounding = ROUNDING.match(" ".join(row["text"].split()))
            if rounding:
                self.rounding = rounding["amount"]
            if attrs.get("id", "").startswith("purchase_list_line_") or set(
                attrs.get("class", "").split()
            ) & {"article", "discount"}:
                self.lines.append(row)


def parse_html(html):
    parser = Lines()
    parser.feed(html)
    items = []
    issues = []
    current = None
    for row in parser.lines:
        attrs = row["attrs"]
        text = " ".join(row["text"].split())
        classes = attrs.get("class", "").split()
        if not text or "currency" in classes:
            continue
        description = " ".join((attrs.get("data-art-description") or "").split())
        explicit_product = bool(description and text.startswith(description))
        quantity = None if explicit_product else QUANTITY.match(text)
        if quantity:
            if current is None:
                issues.append("html_orphan_quantity")
                continue
            current["quantity"] = quantity["quantity"]
            current["currentUnitPrice"] = printed_number(quantity["price"])
            current["isWeight"] = (quantity["unit"] or "").lower() in {"kg", "g"}
            current["quantitySource"] = "printed_quantity_line"
            continue
        tare = TARE.match(text)
        if tare and current:
            current["tareQuantity"] = tare["quantity"]
            continue
        if text.startswith("Záruka:") and current:
            current["warrantyText"] = text.partition(":")[2].strip()
            continue
        if "discount" in classes:
            discount = DISCOUNT.match(text)
            if current is None or discount is None:
                issues.append("html_unparsed_discount")
                continue
            current["discounts"].append(
                {
                    "description": discount["label"].strip(),
                    "amount": discount["amount"].replace(" ", ""),
                    "promotionId": attrs.get("data-promotion-id"),
                }
            )
            continue
        product = product_fields(text, row["text"], attrs)
        if "article" in classes or product:
            current = {
                "name": attrs.get("data-art-description")
                or (product["name"] if product else None),
                "codeInput": attrs.get("data-art-id"),
                "currentUnitPrice": attrs.get("data-unit-price"),
                "quantity": attrs.get("data-art-quantity"),
                "originalAmount": attrs.get("data-art-amount")
                or (product["amount"] if product else None),
                "discounts": [],
                "isWeight": None,
                "parsedFromHtml": True,
                "quantitySource": "html_attribute"
                if attrs.get("data-art-quantity")
                else None,
            }
            # A printed single-unit price is evidence; absent prices never default to 0/1.
            if (
                current["quantity"] is None
                and product
                and current["currentUnitPrice"] is not None
            ):
                try:
                    if Decimal(printed_number(product["amount"])) == Decimal(
                        printed_number(current["currentUnitPrice"])
                    ):
                        current["quantity"] = "1"
                        current["quantitySource"] = "printed_single_unit"
                        current["isWeight"] = False
                except InvalidOperation:
                    issues.append("html_invalid_price")
            items.append(current)
            continue
        net = NET_AMOUNT.match(text)
        if current and current["discounts"] and net:
            current["printedNetAmount"] = net["amount"]
        else:
            issues.append("html_unparsed_line")
    return items, sorted(set(issues)), parser.currency, parser.rounding
