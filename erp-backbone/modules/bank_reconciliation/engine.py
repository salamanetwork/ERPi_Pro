import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from django.db.models import Q, Sum
from backbone.http import Problem, string, identifier
from accounting.models import Line
from accounting.services import ledger_lock, period_for, posted_entry
from .models import BankMatch, BankStatementLine, ClearedLine

ZERO = Decimal("0.00")


def money(value, key):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) >= 10**18 or amount != amount.quantize(Decimal(".01")):
            raise ValueError()
        return amount
    except (InvalidOperation, ValueError):
        raise Problem("invalid_amount", field=key)


def day(value, key):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise Problem("invalid_date", field=key)


def balances(statement):
    sums = statement.lines.aggregate(total=Sum("amount"), cleared=Sum("amount",filter=Q(match__isnull=False)))
    computed = statement.opening_balance + (sums["total"] or ZERO)
    cleared = sums["cleared"] or ZERO
    return {k:format(v,".2f") for k,v in dict(opening=statement.opening_balance, closing=statement.statement_ending_balance,
            computed_ending_balance=computed, cleared=cleared, integrity_difference=statement.statement_ending_balance-computed,
            difference=statement.statement_ending_balance-statement.opening_balance-cleared).items()}


def prepare_lines(statement, rows):
    previous = statement.lines.order_by("-id").first()
    last = previous.date if previous else statement.start
    balance = Decimal(balances(statement)["computed_ending_balance"])
    prepared = []
    for i,row in enumerate(rows,1):
        try:
            when = day(row.get("date"),"date")
            if not last <= when <= statement.end:
                raise Problem("date_outside_statement", field="date")
            amount = money(row.get("amount"),"amount")
            if not amount:
                raise Problem("invalid_amount",field="amount")
            balance += amount
            running = row.get("running_balance")
            if running not in (None,"") and money(running,"running_balance") != balance:
                raise Problem("running_balance_mismatch",field="running_balance")
            prepared.append(BankStatementLine(statement=statement,date=when,description=string(row,"description",300),
                            reference=string(row,"reference",150,required=False),amount=amount,
                            running_balance=balance if running not in (None,"") else None))
            last = when
        except Problem as error:
            raise Problem(error.code,field=f"rows.{i}.{next(iter(error.fields),'value')}") from None
    return prepared


def csv_rows(content, mapping):
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"),newline=""),strict=True)
        columns = reader.fieldnames or []
        if not columns or len(columns)>40 or len(set(columns))!=len(columns) or any(not c.strip() for c in columns):
            raise Problem("invalid_csv_header")
        if mapping is None:
            return columns, []
        if not isinstance(mapping,dict) or any(v not in columns for v in mapping.values()):
            raise Problem("invalid_column_mapping")
        if not {"date","description"} <= mapping.keys() or not (("amount" in mapping) ^ bool({"debit","credit"} & mapping.keys())):
            raise Problem("invalid_column_mapping")
        if len(set(mapping.values()))!=len(mapping):
            raise Problem("invalid_column_mapping")
        rows = []
        for i,raw in enumerate(reader,1):
            if i>5000 or None in raw or None in raw.values():
                raise Problem("invalid_csv_row",field=f"rows.{i}")
            row = {key:raw[col].strip() for key,col in mapping.items()}
            if "amount" not in row:
                debit = money(row.get("debit") or 0,f"rows.{i}.debit")
                credit = money(row.get("credit") or 0,f"rows.{i}.credit")
                if min(debit,credit)<0 or debit and credit:
                    raise Problem("invalid_debit_credit",field=f"rows.{i}")
                row["amount"] = credit-debit
            rows.append(row)
        if not rows:
            raise Problem("empty_csv")
        return columns, rows
    except (csv.Error,UnicodeError):
        raise Problem("invalid_csv")


def eligible(s):
    return Line.objects.filter(account_id=s.profile.account_id,site_id=s.profile.site_id,entry__ledger_id=s.profile.account.ledger_id,
            entry__posted_at__isnull=False,entry__date__lte=s.end,clearedline__isnull=True)


def source(line):
    entry = line.entry
    payment = getattr(entry,"settlement",None)
    ap = getattr(entry,"supplier_settlement",None)
    sale = getattr(getattr(entry,"posting",None),"sale",None)
    if payment:
        return payment.reference,payment.claim.employee.name
    if ap:
        return ap.reference,ap.supplier.name
    if sale:
        return sale.document.number,sale.document.customer.name
    return entry.number,""


def id_list(data, key):
    values = data.get(key)
    if not isinstance(values,list) or not 1<=len(values)<=500:
        raise Problem("invalid_selection",field=key)
    ids = [identifier(x,key) for x in values]
    if len(set(ids))!=len(ids):
        raise Problem("duplicate_selection",field=key)
    return ids


def match(s, bank_ids, gl_ids, user):
    bank = list(s.lines.filter(pk__in=bank_ids,match=None).order_by("pk").select_for_update())
    # Lock base GL rows, then re-read eligibility after waiting on competing transactions.
    list(Line.objects.filter(pk__in=gl_ids,site_id=s.profile.site_id,account_id=s.profile.account_id)
         .order_by("pk").select_for_update().values_list("pk",flat=True))
    gl = list(eligible(s).filter(pk__in=gl_ids))
    if len(bank)!=len(bank_ids) or len(gl)!=len(gl_ids):
        raise Problem("line_unavailable",409)
    if min(len(bank),len(gl))!=1 or sum(x.amount for x in bank)!=sum(x.amount for x in gl):
        raise Problem("match_total_mismatch",409)
    group = BankMatch.objects.create(statement=s,actor=user)
    ClearedLine.objects.bulk_create([ClearedLine(match=group,line=x) for x in gl])
    s.lines.filter(pk__in=bank_ids).update(match=group)
    return group


def adjust(s, data, user):
    ledger = ledger_lock()
    bank = s.lines.select_for_update().filter(pk=identifier(data.get("line_id"),"line_id"),match=None).first()
    if not bank:
        raise Problem("line_unavailable",409)
    if not ledger.activated_at or ledger.cutoff and bank.date<ledger.cutoff:
        raise Problem("finance_not_active",409)
    kind = data.get("kind")
    amount = bank.amount
    if kind=="rounding":
        gl_ids = id_list(data,"gl_ids")
        existing = list(eligible(s).filter(pk__in=gl_ids))
        if len(existing)!=len(gl_ids):
            raise Problem("line_unavailable",409)
        amount -= sum(x.amount for x in existing)
        if not amount or abs(amount)>s.profile.tolerance:
            raise Problem("outside_tolerance",409)
    else:
        gl_ids = []
        if kind != ("fee" if amount<0 else "interest"):
            raise Problem("invalid_adjustment",field="kind")
    offset = s.profile.charges if amount<0 else s.profile.interest
    if not offset.active or not s.profile.account.active:
        raise Problem("inactive_account",409)
    site = s.profile.site_id
    entry = posted_entry(ledger,period_for(ledger,bank.date),bank.date,user.username,
            f"Bank statement {s.pk} / {kind}: {bank.description}", "bank_adj",
            [(s.profile.account,amount,site,None),(offset,-amount,site,None)])
    match(s,[bank.pk],gl_ids+[entry.lines.get(account=s.profile.account).pk],user)
    return entry.pk
