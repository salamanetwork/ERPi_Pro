from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import random
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounting.models import Account, Entry, Ledger, Line, Period, Reference, Rule, SupplierInvoice
from assets.models import Asset, AssetProfile
from backbone.models import Assignment, ModuleState, PrintTemplate, Profile, Role, Site
from backbone.registry import MANIFESTS
from bank_reconciliation.models import BankProfile, BankStatement, BankStatementLine
from billing_core.models import Invoice as BillingInvoice, Line as BillingLine, Receipt as BillingReceipt
from crm.models import Activity as CrmActivity, Lead, Opportunity, Stage, StageHistory
from expenses.models import ExpenseClaim, ExpenseLine
from helpdesk.models import Category as TicketCategory, Comment as TicketComment, SLAPolicy, Ticket
from hr.models import (
    AttendancePolicy,
    Compensation,
    Employee,
    HRReference,
    Leave,
    PayrollRun,
    Payslip,
    WeeklyTimesheet,
)
from inventory.models import (
    Conversion,
    Item,
    Location,
    Movement,
    Position,
    SiteSettings as InvSiteSettings,
    Unit,
    Valuation,
    Warehouse,
)
from manufacturing.models import Bill, Material, Order as MfgOrder, Policy as MfgPolicy
from marketing.models import (
    Campaign,
    Consent,
    Contact,
    ContactEndpoint,
    ProviderAccount,
    Segment,
    SiteSettings as MktSiteSettings,
    Template as MktTemplate,
    TemplateVersion,
)
from parties.models import Party
from projects.models import Baseline, LabourRate, Membership, Project, Task
from publication.models import (
    ContentEntry,
    ContentRevision,
    MenuItem,
    NavigationMenu,
    PublicationSpace,
    PublicSubmission,
    SitemapRefresh,
)
from public_identity.models import ExternalIdentity, ExternalMembership
from purchasing.models import PurchaseOrder, Requisition, Supplier, Template as PurchaseTemplate
from sales.models import Customer, Document as SalesDocument
from scheduling.models import Appointment, PortalBookingRequest, Resource
from subscriptions.models import (
    Contract,
    Cycle,
    DunningPolicy,
    Plan,
    PlanVersion,
    SubscriptionGLMapping,
)
from supply.models import Transfer, TransferLine
from workorders.models import ApprovalTemplate, MaintenanceJob, MaintenancePlan, WorkOrder


class Command(BaseCommand):
    help = "Populate ERPi Pro with a Northwind-scale interconnected bilingual UAE (AED) dataset and live public sites."

    def add_arguments(self, parser):
        parser.add_argument("--admin-username", default="admin", help="Username for the primary admin user.")
        parser.add_argument("--keep-modules", action="store_true", help="Do not enable currently disabled modules.")

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(20261122)
        now = timezone.now()
        today = now.date()
        month_start = today.replace(day=1)
        month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        prev_end = month_start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        prev2_end = prev_start - timedelta(days=1)
        prev2_start = prev2_end.replace(day=1)

        User = get_user_model()
        admin_username = options["admin_username"]
        admin = User.objects.filter(username=admin_username).first() or User.objects.filter(is_superuser=True).first()
        if admin is None:
            admin = User.objects.create_superuser(
                username=admin_username,
                email="admin@erpipro.demo",
                password="ChangeMeNow!2026",
            )
            self.stdout.write(self.style.WARNING(f"Created superuser '{admin_username}' with password 'ChangeMeNow!2026'"))

        Profile.objects.get_or_create(
            user=admin,
            defaults={"iam_display_name": "Ahmed Salama", "language": "en", "theme": "dark", "module_view": "list"},
        )

        # 1. Enable all business modules
        if not options["keep_modules"]:
            for key, meta in MANIFESTS.items():
                if meta.get("category") != "platform_core":
                    ModuleState.objects.update_or_create(module_id=key, defaults={"enabled": True})

        # 2. Sites (4 UAE Branches) & Company Print Templates
        site_specs = [
            ("dxb", "Dubai HQ — المقر الرئيسي دبي", "Jebel Ali Industrial & Corporate Headquarters, Dubai, UAE"),
            ("auh", "Abu Dhabi Branch — فرع أبوظبي", "Mussafah Operations & Service Center, Abu Dhabi, UAE"),
            ("shj", "Sharjah Logistics Hub — مركز الشارقة اللوجستي", "SAIF Zone Regional Distribution & Assembly, Sharjah, UAE"),
            ("rak", "Ras Al Khaimah Depot — مستودع رأس الخيمة", "Al Ghail Heavy Industrial Support Yard, RAK, UAE"),
        ]
        sites = {}
        for code, name, desc in site_specs:
            st, _ = Site.objects.get_or_create(name=name, defaults={"description": desc, "active": True})
            sites[code] = st
        dxb, auh, shj, rak = sites["dxb"], sites["auh"], sites["shj"], sites["rak"]
        site_list = [dxb, auh, shj, rak]

        for lang, company, heading, footer in (
            ("en", "ERPi Pro Gulf Technologies LLC", "Official Business Document", "TRN 100482910400003 · Dubai, United Arab Emirates"),
            ("ar", "شركة إي آر بي آي برو الخليج للتقنية ذ.م.م", "مستند رسمي معتمد", "الرقم الضريبي 100482910400003 · دبي، الإمارات العربية المتحدة"),
        ):
            PrintTemplate.objects.update_or_create(
                kind="default",
                language=lang,
                defaults={"company_name": company, "heading": heading, "footer": footer},
            )

        # 3. Staff Users, Roles & Assignments (11 staff + admin = 12)
        staff_specs = [
            ("sara.ops", "Sara Al Mansoori", "سارة المنصوري", "sara@erpipro.demo", dxb),
            ("omar.eng", "Omar Al Hashimi", "عمر الهاشمي", "omar@erpipro.demo", auh),
            ("layla.fin", "Layla Kareem", "ليلى كريم", "layla@erpipro.demo", dxb),
            ("khaled.sup", "Khaled Al Nuaimi", "خالد النعيمي", "khaled@erpipro.demo", shj),
            ("fatima.hr", "Fatima Al Suwaidi", "فاطمة السويدي", "fatima@erpipro.demo", dxb),
            ("tariq.mfg", "Tariq Al Shamsi", "طارق الشامسي", "tariq@erpipro.demo", shj),
            ("nadia.crm", "Nadia Al Ketbi", "نادية الكتبي", "nadia@erpipro.demo", dxb),
            ("youssef.pm", "Youssef Mahmoud", "يوسف محمود", "youssef@erpipro.demo", auh),
            ("mariam.hd", "Mariam Al Mazrouei", "مريم المزروعي", "mariam@erpipro.demo", dxb),
            ("hassan.qa", "Hassan Al Blooshi", "حسن البلوشي", "hassan@erpipro.demo", rak),
            ("reem.pub", "Reem Al Falasi", "ريم الفلاسي", "reem@erpipro.demo", dxb),
        ]
        staff_users = {"admin": admin}
        all_perms = sorted({perm for m in MANIFESTS.values() for perm in m.get("permissions", [])})
        ops_role, _ = Role.objects.get_or_create(
            name="Operations & Finance Lead",
            defaults={"permissions": all_perms[:60] if all_perms else ["assets.view", "workorders.view"]},
        )
        for uname, disp_en, disp_ar, email, home_site in staff_specs:
            u, created = User.objects.get_or_create(
                username=uname,
                defaults={
                    "email": email,
                    "first_name": disp_en.split()[0],
                    "last_name": " ".join(disp_en.split()[1:]),
                    "is_staff": True,
                },
            )
            if created:
                u.set_unusable_password()
                u.save(update_fields=["password"])
            Profile.objects.get_or_create(user=u, defaults={"iam_display_name": f"{disp_en} ({disp_ar})", "language": "ar"})
            Assignment.objects.get_or_create(user=u, role=ops_role, site=home_site)
            staff_users[uname] = u

        # 4. Accounting Ledger, Chart of Accounts, Periods, Cost Centers & 45+ Journal Entries
        ledger, _ = Ledger.objects.get_or_create(pk=1, defaults={"currency": "AED"})
        if not ledger.activated_at:
            ledger.currency = "AED"
            ledger.cutoff = prev2_start
            ledger.activated_at = now
            ledger.activated_by = admin.username
            ledger.save(update_fields=["currency", "cutoff", "activated_at", "activated_by"])

        p_prev2, _ = Period.objects.get_or_create(ledger=ledger, start=prev2_start, defaults={"end": prev2_end})
        p_prev1, _ = Period.objects.get_or_create(ledger=ledger, start=prev_start, defaults={"end": prev_end})
        p_curr, _ = Period.objects.get_or_create(ledger=ledger, start=month_start, defaults={"end": month_end})

        acc_1000 = Account.objects.get(ledger=ledger, code="1000")
        acc_2000 = Account.objects.get(ledger=ledger, code="2000")
        acc_4000 = Account.objects.get(ledger=ledger, code="4000")
        acc_5000 = Account.objects.get(ledger=ledger, code="5000")

        extra_accounts = [
            ("1110", "Emirates NBD Corporate Bank (AED)", "asset", acc_1000, True, False),
            ("1120", "Abu Dhabi Commercial Bank ADCB (AED)", "asset", acc_1000, True, False),
            ("1300", "Accounts Receivable — الذمم المدينة", "asset", acc_1000, True, True),
            ("1400", "Prepaid Expenses & Deposits", "asset", acc_1000, True, False),
            ("2200", "Accounts Payable — الذمم الدائنة", "liability", acc_2000, True, True),
            ("2300", "UAE VAT Output Payable (5%)", "liability", acc_2000, True, False),
            ("2310", "UAE VAT Input Recoverable (5%)", "asset", acc_1000, True, False),
            ("2400", "Deferred Subscription Revenue", "liability", acc_2000, True, False),
            ("2500", "Salaries & Wages Payable", "liability", acc_2000, True, False),
            ("4200", "Equipment & Solution Sales", "revenue", acc_4000, True, False),
            ("4300", "Subscription & Maintenance Revenue", "revenue", acc_4000, True, False),
            ("4400", "Field Commissioning & Engineering Services", "revenue", acc_4000, True, False),
            ("5300", "Cost of Goods Sold (COGS)", "expense", acc_5000, True, False),
            ("5400", "Salaries & Payroll Expense", "expense", acc_5000, True, False),
            ("5500", "Bank Charges & Fees", "expense", acc_5000, True, False),
            ("5600", "Travel & Site Operational Expense", "expense", acc_5000, True, False),
            ("5700", "Warehouse Rent & Utilities", "expense", acc_5000, True, False),
        ]
        accts = {a.code: a for a in Account.objects.filter(ledger=ledger)}
        for code, name, kind, parent, detail, control in extra_accounts:
            obj, _ = Account.objects.get_or_create(
                ledger=ledger,
                code=code,
                defaults={"name": name, "kind": kind, "parent": parent, "detail": detail, "control": control, "active": True},
            )
            accts[code] = obj

        cc_specs = [
            ("CC-DXB-OPS", "Dubai Operations — عمليات دبي"),
            ("CC-DXB-ENG", "Engineering & Automation — الهندسة والأتمتة"),
            ("CC-AUH-OPS", "Abu Dhabi Branch — فرع أبوظبي"),
            ("CC-SHJ-MFG", "Sharjah Assembly & Logistics — الشارقة"),
            ("CC-RAK-DEP", "RAK Industrial Support — رأس الخيمة"),
        ]
        ccs = {}
        for code, name in cc_specs:
            ref, _ = Reference.objects.get_or_create(ledger=ledger, kind="cost_center", code=code, starts=None, defaults={"name": name})
            ccs[code] = ref

        Reference.objects.get_or_create(
            ledger=ledger, kind="tax", code="VAT5", starts=date(2018, 1, 1),
            defaults={"name": "UAE Standard VAT 5%", "rate": Decimal("0.0500")},
        )

        # Generate 42 balanced journal entries across periods & sites
        je_templates = [
            ("Capital & Working Liquidity Injection — Emirates NBD", p_prev2, prev2_start + timedelta(days=2), dxb, ccs["CC-DXB-OPS"],
             [(accts["1110"], Decimal("1250000.00")), (accts["3100"], Decimal("-1250000.00"))]),
            ("ADCB Regional Operating Account Funding", p_prev2, prev2_start + timedelta(days=3), auh, ccs["CC-AUH-OPS"],
             [(accts["1120"], Decimal("450000.00")), (accts["3100"], Decimal("-450000.00"))]),
        ]
        rev_accounts = [accts["4200"], accts["4300"], accts["4400"]]
        exp_accounts = [accts["5300"], accts["5600"], accts["5700"], accts["5500"]]
        cc_vals = list(ccs.values())
        for idx in range(1, 41):
            st = site_list[idx % len(site_list)]
            cc = cc_vals[idx % len(cc_vals)]
            if idx <= 12:
                per, base_d = p_prev2, prev2_start + timedelta(days=(idx * 2) % 26)
            elif idx <= 26:
                per, base_d = p_prev1, prev_start + timedelta(days=(idx * 2) % 27)
            else:
                per, base_d = p_curr, month_start + timedelta(days=min((idx - 26), max(1, today.day - 1)))

            if idx % 3 != 0:
                net = Decimal(str(12000 + idx * 1850))
                vat = (net * Decimal("0.05")).quantize(Decimal("0.01"))
                gross = net + vat
                rev_acc = rev_accounts[idx % len(rev_accounts)]
                cash_or_ar = accts["1110"] if idx % 2 == 0 else accts["1300"]
                desc = f"Northwind-Gulf Batch Revenue #{idx:03d} — {st.name.split('—')[0].strip()}"
                lines = [(cash_or_ar, gross), (rev_acc, -net), (accts["2300"], -vat)]
            else:
                amt = Decimal(str(3200 + idx * 410))
                exp_acc = exp_accounts[idx % len(exp_accounts)]
                bank_acc = accts["1110"] if st in (dxb, shj) else accts["1120"]
                desc = f"Northwind-Gulf Operational Expenditure #{idx:03d} — {st.name.split('—')[0].strip()}"
                lines = [(exp_acc, amt), (bank_acc, -amt)]
            je_templates.append((desc, per, base_d, st, cc, lines))

        for desc, per, jdate, st, cc, line_items in je_templates:
            if not Entry.objects.filter(ledger=ledger, description=desc).exists():
                entry = Entry.objects.create(
                    ledger=ledger,
                    period=per,
                    date=jdate,
                    source_date=jdate,
                    description=desc,
                    kind="manual",
                    created_by=admin.username,
                    posted_by=admin.username,
                    posted_at=now,
                )
                Line.objects.bulk_create([
                    Line(entry=entry, account=acc, amount=amt, site=st, cost_center=cc)
                    for acc, amt in line_items
                ])

        # 5. Live Public Sites (PublicationSpace) — 3 Distinct Portals
        space_specs = [
            ("gulf-portal", "ERPi Pro Gulf Industrial & SCADA Portal", "en", dxb),
            ("abu-dhabi-hub", "Abu Dhabi Energy & Calibration Services Hub", "en", auh),
            ("sharjah-logistics", "Sharjah Automation & Fleet Supply Portal", "ar", shj),
        ]
        spaces = {}
        for scode, sname, slang, ssite in space_specs:
            sp, _ = PublicationSpace.objects.get_or_create(
                code=scode,
                defaults={"name": sname, "default_language": slang, "internal_site": ssite, "active": True},
            )
            spaces[scode] = sp
        sp_gulf, sp_auh, sp_shj = spaces["gulf-portal"], spaces["abu-dhabi-hub"], spaces["sharjah-logistics"]

        # 6. Parties (36 Organizations: Customers, Suppliers, Dual + Portal-Linked Parties)
        party_seed = [
            # Portal-scoped parties for gulf-portal (dxb, sp_gulf)
            ("P-PORTAL-01", "customer", "Al Futtaim Industrial Logistics LLC", "الفطيم للخدمات اللوجستية الصناعية", "+971-4-8812340", "portal.futtaim@erpipro.demo", "100293847100003", "Jebel Ali Free Zone South, Dubai", dxb, sp_gulf),
            ("P-PORTAL-02", "customer", "DP World Cold Chain Terminals", "موانئ دبي العالمية لمحطات التبريد", "+971-4-8815500", "portal.dpcold@erpipro.demo", "100293847200003", "Jebel Ali Port Terminal 2, Dubai", dxb, sp_gulf),
            ("P-PORTAL-03", "customer", "Emaar District Cooling Utilities", "إعمار لمرافق تبريد المناطق", "+971-4-3673300", "portal.emaardc@erpipro.demo", "100293847300003", "Downtown Dubai, UAE", dxb, sp_gulf),
            # Portal-scoped parties for abu-dhabi-hub (auh, sp_auh)
            ("P-PORTAL-04", "customer", "Emirates Energy & Water Systems", "الإمارات لأنظمة الطاقة والمياه", "+971-2-6549810", "portal.eews@erpipro.demo", "100384726100003", "Mussafah Industrial Area M14, Abu Dhabi", auh, sp_auh),
            ("P-PORTAL-05", "customer", "Taweelah Desalination & Power Co.", "شركة الطويلة لتحلية المياه والطاقة", "+971-2-5094400", "portal.taweelah@erpipro.demo", "100384726200003", "Al Taweelah Industrial Zone, Abu Dhabi", auh, sp_auh),
            # Portal-scoped party for sharjah-logistics (shj, sp_shj)
            ("P-PORTAL-06", "customer", "Sharjah Maritime & Freezone Cargo", "الشارقة للشحن البحري والمناطق الحرة", "+971-6-5578900", "portal.shjcargo@erpipro.demo", "100484726100003", "SAIF Zone Gate 3, Sharjah", shj, sp_shj),
            # General ERP Customers & Suppliers across sites
            ("P-CUST-07", "both", "Gulf Smart Buildings & MEP", "الخليج للمباني الذكية والأعمال الكهروميكانيكية", "+971-4-3398120", "ops@gulfsmartmep.demo", "100472819300003", "Business Bay, Dubai", dxb, None),
            ("P-CUST-08", "customer", "Al Ain Dairy & Food Processing", "العين للألبان وتصنيع الأغذية", "+971-3-7812200", "eng@alaindairy.demo", "100511223300003", "Al Ain Industrial City, Abu Dhabi", auh, None),
            ("P-CUST-09", "customer", "RAK Ceramics & Kiln Automation", "سيراميك رأس الخيمة وأتمتة الأفران", "+971-7-2446100", "maint@rakceramics-demo.ae", "100611223300003", "Al Jazeera Al Hamra, RAK", rak, None),
            ("P-CUST-10", "customer", "Sharjah Oasis Bottling Plant", "مصنع واحة الشارقة لتعبئة المياه", "+971-6-5349010", "plant@shjoasis.demo", "100711223300003", "Industrial Area 15, Sharjah", shj, None),
            ("P-CUST-11", "customer", "Dubai Metro Rail Systems Maintenance", "صيانة أنظمة مترو دبي", "+971-4-2901122", "systems@dmrail.demo", "100811223300003", "Al Rashidiya Depot, Dubai", dxb, None),
            ("P-CUST-12", "customer", "Fujairah Bunkering & Tank Terminals", "فجيرة لمحطات الوقود والخزانات", "+971-9-2228400", "scada@fujtanks.demo", "100911223300003", "Fujairah Oil Industry Zone", shj, None),
            ("P-CUST-13", "customer", "Al Habtoor Hospitality Engineering", "الحبتور لهندسة الضيافة", "+971-4-3941444", "bms@habtooreng.demo", "101011223300003", "Al Habtoor City, Dubai", dxb, None),
            ("P-CUST-14", "customer", "Ruwais Petrochemical Logistics", "الرويس للخدمات البتروكيماوية", "+971-2-8774100", "inst@ruwaispetro.demo", "101111223300003", "Al Ruwais, Abu Dhabi", auh, None),
            ("P-CUST-15", "customer", "Julphar Pharmaceutical Cleanrooms", "جلفار للغرف الدوائية النظيفة", "+971-7-2461461", "hvac@julphar-demo.ae", "101211223300003", "Digdaga, Ras Al Khaimah", rak, None),
            ("P-CUST-16", "both", "Transguard Facility Automation", "ترانس جارد لأتمتة المرافق", "+971-4-7030500", "fm@transguard-demo.ae", "101311223300003", "Dubai Airport Freezone", dxb, None),
            ("P-SUP-01", "supplier", "Siemens Middle East Automation", "سيمنز الشرق الأوسط للأتمتة", "+971-4-3660000", "orders@siemens-me.demo", "100112233400003", "Expo City Corridor, Dubai", dxb, None),
            ("P-SUP-02", "supplier", "Yokogawa Gulf Process Control", "يوكوجاوا الخليج للتحكم الصناعي", "+971-2-6102200", "sales@yokogawa-gulf.demo", "100556677800003", "ADGM Square, Abu Dhabi", auh, None),
            ("P-SUP-03", "supplier", "Schneider Electric Gulf FZE", "شنايدر إلكتريك الخليج", "+971-4-7099100", "supply@schneider-gulf.demo", "100556677900003", "JAFZA North, Dubai", dxb, None),
            ("P-SUP-04", "supplier", "Emerson Process Management MEA", "إيمرسون لإدارة العمليات", "+971-4-8118100", "quotes@emerson-mea.demo", "100556678000003", "Jebel Ali South, Dubai", dxb, None),
            ("P-SUP-05", "supplier", "ABB Industries LLC UAE", "إيه بي بي للصناعات الإمارات", "+971-2-4938000", "orders@abb-uae.demo", "100556678100003", "Mussafah, Abu Dhabi", auh, None),
            ("P-SUP-06", "supplier", "Phoenix Contact Middle East", "فينيكس كونتاكت الشرق الأوسط", "+971-4-4370324", "sales@phoenix-me.demo", "100556678200003", "Dubai Silicon Oasis", dxb, None),
            ("P-SUP-07", "supplier", "Endress+Hauser Flow & Level", "إندريس هاوزر للقياس الصناعي", "+971-4-3724700", "uae@endress-demo.com", "100556678300003", "Dubai Science Park", dxb, None),
            ("P-SUP-08", "supplier", "Ducab High Voltage & Control Cables", "دوكاب للكابلات الصناعية", "+971-4-8158888", "industrial@ducab-demo.ae", "100556678400003", "Jebel Ali Industrial, Dubai", shj, None),
        ]
        parties = {}
        for key, ptype, name_en, name_ar, phone, email, tax_id, addr, st, sp in party_seed:
            p, _ = Party.objects.get_or_create(
                site=st,
                identity_number=tax_id,
                defaults={
                    "space": sp,
                    "party_type": ptype,
                    "name_en": name_en,
                    "name_ar": name_ar,
                    "phone": phone,
                    "email": email,
                    "address": addr,
                    "active": True,
                },
            )
            if sp and p.space_id != sp.pk:
                p.space = sp
                p.save(update_fields=["space"])
            parties[key] = p

        # 7. Inventory: Units, 8 Warehouses, 16 Locations, 36 Industrial Items, Positions & Valuations
        for st in site_list:
            InvSiteSettings.objects.get_or_create(site=st, defaults={"timezone": "Asia/Dubai"})

        unit_pc, _ = Unit.objects.get_or_create(code="PC", defaults={"name": "Piece"})
        unit_set, _ = Unit.objects.get_or_create(code="SET", defaults={"name": "Set"})
        unit_mtr, _ = Unit.objects.get_or_create(code="MTR", defaults={"name": "Meter"})
        unit_box, _ = Unit.objects.get_or_create(code="BOX", defaults={"name": "Box (10 pcs)"})
        Conversion.objects.get_or_create(item=None, unit=unit_box, base_unit=unit_pc, defaults={"factor": Decimal("10")})

        wh_specs = [
            ("WH-DXB-01", dxb, "Dubai Main Warehouse — مستودع دبي الرئيسي", [("A-01", "Rack A1 — Transmitters"), ("B-01", "Rack B1 — PLCs & HMIs")]),
            ("WH-DXB-MFG", dxb, "Dubai Assembly Floor — صالة التجميع دبي", [("RM-01", "Raw Assembly Kits"), ("FG-01", "Finished Telemetry Panels")]),
            ("WH-AUH-01", auh, "Abu Dhabi Service Depot — مستودع أبوظبي", [("S-01", "Shelf S1 — Field Spares"), ("V-01", "Bay V1 — Actuators & Valves")]),
            ("WH-SHJ-01", shj, "Sharjah Logistics Hub — مستودع الشارقة", [("L-01", "Zone L1 — Bulk Cables"), ("L-02", "Zone L2 — Switchgear")]),
            ("WH-RAK-01", rak, "RAK Industrial Yard — مستودع رأس الخيمة", [("R-01", "Bay R1 — Heavy Motors & VFDs"), ("R-02", "Bay R2 — Kiln Sensors")]),
        ]
        locs = {}
        for wcode, st, wname, loc_list in wh_specs:
            wh, _ = Warehouse.objects.get_or_create(site=st, code=wcode, defaults={"name": wname})
            for lcode, lname in loc_list:
                loc, _ = Location.objects.get_or_create(warehouse=wh, code=lcode, defaults={"name": lname})
                locs[f"{wcode}:{lcode}"] = (st, loc)

        catalog = [
            ("INV-PLC-1500", "Siemens S7-1500 CPU 1515-2 PN Controller", unit_pc, "WH-DXB-01:B-01", Decimal("28"), Decimal("6200.00")),
            ("INV-PLC-1200", "Siemens S7-1200 CPU 1214C DC/DC/DC", unit_pc, "WH-DXB-01:B-01", Decimal("45"), Decimal("1850.00")),
            ("INV-IO-ET200", "SIMATIC ET 200SP Distributed I/O Module 16DI/16DO", unit_pc, "WH-DXB-01:B-01", Decimal("60"), Decimal("920.00")),
            ("INV-HMI-1200", "12-inch Industrial Touch HMI Panel IP66", unit_pc, "WH-DXB-01:B-01", Decimal("22"), Decimal("3100.00")),
            ("INV-HMI-0700", "7-inch Compact Operator HMI Terminal", unit_pc, "WH-SHJ-01:L-02", Decimal("34"), Decimal("1650.00")),
            ("INV-DPT-3051", "Rosemount 3051S Differential Pressure Transmitter", unit_pc, "WH-DXB-01:A-01", Decimal("32"), Decimal("4850.00")),
            ("INV-TT-644", "Rosemount 644 HART Head-Mount Temperature Transmitter", unit_pc, "WH-DXB-01:A-01", Decimal("40"), Decimal("1450.00")),
            ("INV-RTD-PT100", "Industrial Pt100 Duplex RTD Thermowell Assembly", unit_pc, "WH-RAK-01:R-02", Decimal("55"), Decimal("680.00")),
            ("INV-MAG-8705", "Electromagnetic Flowmeter DN100 Flanged PN16", unit_pc, "WH-AUH-01:V-01", Decimal("14"), Decimal("7900.00")),
            ("INV-RDR-5408", "Non-Contact Radar Level Transmitter 80GHz", unit_pc, "WH-AUH-01:S-01", Decimal("16"), Decimal("6400.00")),
            ("INV-MOV-AUMA", "AUMA SA 07.6 Multi-Turn Motor Operated Valve Actuator", unit_pc, "WH-AUH-01:V-01", Decimal("15"), Decimal("9400.00")),
            ("INV-VLV-CTRL", "2-Inch Globe Control Valve with Pneumatic Positioner", unit_pc, "WH-AUH-01:V-01", Decimal("12"), Decimal("5300.00")),
            ("INV-VFD-15KW", "ABB ACS580 Variable Frequency Drive 15kW 3-Phase", unit_pc, "WH-RAK-01:R-01", Decimal("18"), Decimal("4950.00")),
            ("INV-VFD-45KW", "ABB ACS580 Variable Frequency Drive 45kW IP55", unit_pc, "WH-RAK-01:R-01", Decimal("9"), Decimal("11800.00")),
            ("INV-PSU-24V", "SITOP 24V DC 20A Redundant Industrial Power Supply", unit_pc, "WH-DXB-MFG:RM-01", Decimal("50"), Decimal("740.00")),
            ("INV-SW-SCAL", "SCALANCE XB208 Managed Industrial Ethernet Switch", unit_pc, "WH-DXB-MFG:RM-01", Decimal("26"), Decimal("2350.00")),
            ("INV-GTW-MOD", "Modbus RTU to Modbus TCP/IP Industrial Gateway", unit_pc, "WH-AUH-01:S-01", Decimal("30"), Decimal("1150.00")),
            ("INV-SPD-24V", "Phoenix Contact Plugtrab 24V Loop Surge Arrester", unit_pc, "WH-DXB-01:A-01", Decimal("110"), Decimal("210.00")),
            ("INV-RLY-SLIM", "24V DC Slim Interface Relay Module 6A", unit_box, "WH-SHJ-01:L-02", Decimal("40"), Decimal("320.00")),
            ("INV-CBL-SHLD", "Armored Instrumentation Shielded Cable 2x1.5mm", unit_mtr, "WH-SHJ-01:L-01", Decimal("3500"), Decimal("14.50")),
            ("INV-CBL-PROF", "PROFIBUS DP / RS485 Violet Shielded Bus Cable", unit_mtr, "WH-SHJ-01:L-01", Decimal("1800"), Decimal("18.00")),
            ("INV-ENC-IP66", "Rittal Stainless Steel Control Enclosure 1200x800x300", unit_pc, "WH-DXB-MFG:RM-01", Decimal("14"), Decimal("3400.00")),
            ("INV-CAL-LOOP", "Fluke 789 ProcessMeter Loop Calibrator Kit", unit_set, "WH-AUH-01:S-01", Decimal("8"), Decimal("4600.00")),
            ("INV-PNL-SCADA", "Assembled RTU & SCADA Telemetry Cabinet", unit_set, "WH-DXB-MFG:FG-01", Decimal("9"), Decimal("24500.00")),
            ("INV-PNL-MCC", "Assembled Intelligent Motor Control Center (MCC) Bay", unit_set, "WH-DXB-MFG:FG-01", Decimal("5"), Decimal("38000.00")),
            ("INV-PNL-BMS", "Assembled Chiller Plant DDC/PLC Control Panel", unit_set, "WH-DXB-MFG:FG-01", Decimal("7"), Decimal("19800.00")),
        ]
        items = {}
        for code, name, unit, loc_key, qty, unit_cost in catalog:
            st, loc = locs[loc_key]
            itm, _ = Item.objects.get_or_create(code=code, defaults={"name": name, "base_unit": unit, "active": True})
            items[code] = itm
            total_val = (qty * unit_cost).quantize(Decimal("0.01"))
            Position.objects.get_or_create(site=st, item=itm, location=loc, defaults={"quantity": qty})
            Valuation.objects.get_or_create(site=st, item=itm, defaults={"quantity": qty, "value": total_val})
            if not Movement.objects.filter(site=st, item=itm, location=loc, kind="opening").exists():
                Movement.objects.create(
                    site=st,
                    item=itm,
                    location=loc,
                    kind="opening",
                    quantity=qty,
                    value=total_val,
                    actor=admin.username,
                    actor_id=admin.pk,
                    occurred_at=now - timedelta(days=45),
                    business_date=prev2_start + timedelta(days=1),
                    facts={"source": "seed_demo_northwind", "unit_cost": str(unit_cost)},
                )

        # 8. Purchasing: 8 Suppliers, 22 Requisitions, 18 Purchase Orders & 10 Supplier Invoices
        supplier_keys = ["P-SUP-01", "P-SUP-02", "P-SUP-03", "P-SUP-04", "P-SUP-05", "P-SUP-06", "P-SUP-07", "P-SUP-08"]
        suppliers = []
        for idx, pkey in enumerate(supplier_keys, start=1):
            p = parties[pkey]
            sup, _ = Supplier.objects.get_or_create(
                code=f"SUP-{idx:03d}",
                defaults={
                    "party": p,
                    "name": p.name_en,
                    "legal_name": f"{p.name_en} LLC",
                    "currency_default": "AED",
                    "contact": f"{p.email} · {p.phone}",
                    "tax_id": p.identity_number,
                },
            )
            suppliers.append(sup)

        PurchaseTemplate.objects.get_or_create(
            currency="AED",
            version=1,
            defaults={"threshold": Decimal("10000.00"), "steps": [{"role": "Operations & Finance Lead", "label": "Finance Approval"}]},
        )

        item_codes = list(items.keys())
        for idx in range(1, 23):
            st = site_list[idx % len(site_list)]
            sup = suppliers[idx % len(suppliers)]
            ic1 = item_codes[idx % len(item_codes)]
            ic2 = item_codes[(idx + 4) % len(item_codes)]
            q1, p1 = Decimal(str(2 + (idx % 6))), Decimal(str(1200 + (idx * 250) % 5000))
            q2, p2 = Decimal(str(4 + (idx % 8))), Decimal(str(450 + (idx * 180) % 2400))
            total = (q1 * p1 + q2 * p2).quantize(Decimal("0.01"))
            pr_status = (
                Requisition.Status.DRAFT if idx > 19
                else Requisition.Status.IN_REVIEW if idx > 16
                else Requisition.Status.APPROVED
            )
            lines_payload = [
                {"line_id": 1, "item_code": ic1, "description": items[ic1].name, "quantity": str(q1), "unit_price": str(p1)},
                {"line_id": 2, "item_code": ic2, "description": items[ic2].name, "quantity": str(q2), "unit_price": str(p2)},
            ]
            pr, _ = Requisition.objects.get_or_create(
                number=f"PR-2026-{idx:04d}",
                defaults={
                    "title": f"Batch Replenishment #{idx:02d} — {sup.name.split()[0]} ({st.name.split('—')[0].strip()})",
                    "site": st,
                    "currency": "AED",
                    "lines": lines_payload,
                    "total": total,
                    "status": pr_status,
                },
            )
            if pr_status == Requisition.Status.APPROVED and idx <= 16:
                po_status = PurchaseOrder.Status.DRAFT if idx > 13 else PurchaseOrder.Status.ISSUED
                po, _ = PurchaseOrder.objects.get_or_create(
                    number=f"PO-2026-{idx:04d}",
                    defaults={
                        "source_pr": pr,
                        "site": st,
                        "supplier": sup,
                        "title": f"PO #{idx:02d} — {sup.name}",
                        "currency": "AED",
                        "lines": lines_payload,
                        "total": total,
                        "status": po_status,
                        "issued_at": now - timedelta(days=(20 - idx)) if po_status == PurchaseOrder.Status.ISSUED else None,
                        "issued_by": admin if po_status == PurchaseOrder.Status.ISSUED else None,
                        "snapshot": {"supplier": {"code": sup.code, "name": sup.name}},
                    },
                )
                if po_status == PurchaseOrder.Status.ISSUED and idx <= 10:
                    inv_num = f"VINV-2026-{idx:04d}"
                    SupplierInvoice.objects.get_or_create(
                        supplier=sup,
                        supplier_number_key=inv_num.lower(),
                        defaults={
                            "order": po,
                            "site": st,
                            "supplier_number": inv_num,
                            "invoice_date": today - timedelta(days=(15 - idx)),
                            "due_date": today + timedelta(days=(15 + idx)),
                            "created_by": admin,
                            "status": "draft" if idx > 7 else "posted",
                            "draft_lines": lines_payload,
                            "declared_total": total,
                            "total": total,
                        },
                    )

        # 9. Sales: 16 Customers & 64 Sales Documents (Quotes -> Orders -> Deliveries -> Invoices -> Receipts)
        cust_keys = [k for k in parties if k.startswith("P-PORTAL-") or k.startswith("P-CUST-")]
        customers = []
        for idx, pkey in enumerate(cust_keys, start=1):
            p = parties[pkey]
            c, _ = Customer.objects.get_or_create(
                code=f"CUST-{idx:03d}",
                defaults={
                    "party": p,
                    "name": p.name_en,
                    "legal_name": f"{p.name_en} LLC",
                    "contact": f"{p.email} · {p.phone}",
                    "billing": p.address,
                    "shipping": p.address,
                    "tax_id": p.identity_number,
                    "terms": 30 if idx % 2 == 0 else 45,
                },
            )
            customers.append(c)

        for idx in range(1, 21):
            cust = customers[idx % len(customers)]
            st = cust.party.site
            ic1 = item_codes[(idx * 2) % len(item_codes)]
            ic2 = item_codes[(idx * 2 + 3) % len(item_codes)]
            q1, p1 = Decimal(str(1 + (idx % 4))), Decimal(str(4200 + (idx * 650) % 18000))
            q2, p2 = Decimal(str(2 + (idx % 5))), Decimal(str(1400 + (idx * 320) % 6500))
            sub = (q1 * p1 + q2 * p2).quantize(Decimal("0.01"))
            gross = (sub * Decimal("1.05")).quantize(Decimal("0.01"))
            doc_date = today - timedelta(days=max(1, 55 - idx * 2))
            lines_json = [
                {"item_code": ic1, "description": items[ic1].name, "qty": str(q1), "price": str(p1), "tax_rate": "0.05"},
                {"item_code": ic2, "description": items[ic2].name, "qty": str(q2), "price": str(p2), "tax_rate": "0.05"},
            ]
            tag_reason = f"Northwind-Gulf Deal #{idx:03d} — {cust.name.split()[0]}"
            qt = SalesDocument.objects.filter(site=st, customer=cust, kind="quote", reason=tag_reason).first()
            if qt is None:
                qt = SalesDocument.objects.create(
                    kind="quote",
                    site=st,
                    customer=cust,
                    date=doc_date,
                    expires=doc_date + timedelta(days=30),
                    status="accepted" if idx <= 15 else "sent",
                    lines=lines_json,
                    amount=gross,
                    reason=tag_reason,
                    actor=admin.username,
                )
            if idx <= 15:
                so = SalesDocument.objects.filter(source=qt, kind="order").first()
                if so is None:
                    so = SalesDocument.objects.create(
                        kind="order",
                        site=st,
                        customer=cust,
                        source=qt,
                        date=doc_date + timedelta(days=2),
                        status="confirmed",
                        lines=lines_json,
                        amount=gross,
                        reason=f"SO for {tag_reason}",
                        actor=admin.username,
                    )
                if idx <= 11:
                    if not SalesDocument.objects.filter(order=so, kind="delivery").exists():
                        SalesDocument.objects.create(
                            kind="delivery",
                            site=st,
                            customer=cust,
                            source=so,
                            order=so,
                            date=doc_date + timedelta(days=5),
                            status="delivered",
                            lines=lines_json,
                            amount=gross,
                            reason=f"DN for {tag_reason}",
                            actor=admin.username,
                        )
                    inv = SalesDocument.objects.filter(order=so, kind="invoice").first()
                    if inv is None:
                        inv = SalesDocument.objects.create(
                            kind="invoice",
                            site=st,
                            customer=cust,
                            source=so,
                            order=so,
                            date=doc_date + timedelta(days=6),
                            due=doc_date + timedelta(days=36),
                            status="paid" if idx <= 6 else "issued",
                            lines=lines_json,
                            amount=gross,
                            reason=f"Tax Invoice for {tag_reason}",
                            actor=admin.username,
                        )
                    if idx <= 6 and not SalesDocument.objects.filter(source=inv, kind="receipt").exists():
                        SalesDocument.objects.create(
                            kind="receipt",
                            site=st,
                            customer=cust,
                            source=inv,
                            order=so,
                            date=doc_date + timedelta(days=12),
                            status="posted",
                            lines=[],
                            amount=gross,
                            reason=f"Bank Receipt for {tag_reason}",
                            actor=admin.username,
                        )

        # 10. Assets (20 Industrial Assets), Maintenance Plans (12) & Work Orders (20)
        asset_templates = [
            ("AST-DXB-001", "Boiler Feedwater Pump Station #1", dxb, Decimal("145000.00"), "Rotating Equipment", "Jebel Ali Utility Bay 1"),
            ("AST-DXB-002", "CNC Busbar & Panel Fabrication Center", dxb, Decimal("220000.00"), "Manufacturing Machinery", "Dubai Assembly Floor"),
            ("AST-DXB-003", "1500 kVA Caterpillar Standby Diesel Generator", dxb, Decimal("310000.00"), "Power Generation", "Jebel Ali Power Yard"),
            ("AST-DXB-004", "Chiller Plant Primary Centrifugal Compressor A", dxb, Decimal("275000.00"), "HVAC & Cooling", "Dubai Plant Room 1"),
            ("AST-DXB-005", "Automated High-Bay AS/RS Pallet Crane #1", dxb, Decimal("410000.00"), "Warehouse Automation", "Dubai Main Warehouse"),
            ("AST-DXB-006", "Proxmox & Core ERP High-Availability Server Rack", dxb, Decimal("95000.00"), "IT & SCADA Infrastructure", "Dubai HQ Data Room"),
            ("AST-AUH-001", "HART 475 & Deadweight Pressure Calibration Bench", auh, Decimal("48000.00"), "Test & Calibration", "Mussafah Lab Room 2"),
            ("AST-AUH-002", "RO Desalination High-Pressure Booster Pump B", auh, Decimal("185000.00"), "Rotating Equipment", "Mussafah Test Skid 1"),
            ("AST-AUH-003", "Mobile Field Commissioning & Diagnostic Van #1", auh, Decimal("135000.00"), "Fleet & Field Service", "Abu Dhabi Fleet Bay"),
            ("AST-AUH-004", "Ultrasonic Flow Calibration Loop Rig", auh, Decimal("165000.00"), "Test & Calibration", "Mussafah Hydraulics Bay"),
            ("AST-SHJ-001", "3-Ton Electric Reach Forklift Fleet Unit #1", shj, Decimal("88000.00"), "Material Handling", "SAIF Zone Warehouse A"),
            ("AST-SHJ-002", "3-Ton Electric Reach Forklift Fleet Unit #2", shj, Decimal("88000.00"), "Material Handling", "SAIF Zone Warehouse B"),
            ("AST-SHJ-003", "Automated Cable Spooling & Cutting Line", shj, Decimal("112000.00"), "Manufacturing Machinery", "Sharjah Cable Bay"),
            ("AST-SHJ-004", "500 kW Rooftop Solar Inverter & SCADA Skid", shj, Decimal("195000.00"), "Power Generation", "Sharjah Hub Roof"),
            ("AST-RAK-001", "Rotary Kiln Burner Management Control Skid", rak, Decimal("240000.00"), "Process Control", "Al Ghail Yard Bay 1"),
            ("AST-RAK-002", "10-Ton Overhead Bridge Crane — Heavy Bay", rak, Decimal("175000.00"), "Material Handling", "RAK Main Hangar"),
        ]
        assets = []
        for tag, name, st, cost, eq_type, floc in asset_templates:
            ast, _ = Asset.objects.get_or_create(
                tag=tag,
                defaults={
                    "name": name,
                    "purchased_on": date(2024, 6, 15),
                    "purchase_cost": cost,
                    "purchase_currency": "AED",
                    "purchase_reference": f"CAPEX-{tag}",
                    "site": st,
                },
            )
            AssetProfile.objects.get_or_create(
                asset=ast,
                defaults={"equipment_type": eq_type, "functional_location": floc, "state": "active"},
            )
            assets.append(ast)

        ApprovalTemplate.objects.get_or_create(
            version=1,
            defaults={"steps": [{"role": "Operations & Finance Lead", "label": "Maintenance Sign-off"}]},
        )
        wo_statuses = [WorkOrder.Status.DRAFT, WorkOrder.Status.SUBMITTED, WorkOrder.Status.IN_REVIEW, WorkOrder.Status.APPROVED]
        work_orders = []
        for idx, ast in enumerate(assets, start=1):
            wo_title = f"PM & Diagnostic Inspection #{idx:02d} — {ast.name}"
            wo, _ = WorkOrder.objects.get_or_create(
                title=wo_title,
                defaults={
                    "description": f"Scheduled mechanical, electrical, and instrumentation loop verification for {ast.tag}.",
                    "site": ast.site,
                    "asset_reference": ast.pk,
                    "asset_snapshot": f"{ast.tag} · {ast.name}",
                    "status": wo_statuses[idx % len(wo_statuses)],
                    "submitted_at": now - timedelta(hours=idx * 5) if idx % len(wo_statuses) != 0 else None,
                },
            )
            work_orders.append(wo)
            if idx <= 12:
                pm, _ = MaintenancePlan.objects.get_or_create(
                    asset=ast,
                    title=f"Monthly Preventive Routine — {ast.tag}",
                    defaults={
                        "first_due": month_start,
                        "interval": 1,
                        "frequency": "monthly",
                        "checklist": ["Inspect mechanical seals & bearings", "Verify 4-20mA transmitter loop accuracy", "Test safety interlocks"],
                        "spare_parts": [{"code": "INV-DPT-3051", "qty": 1}],
                        "active": True,
                    },
                )
                MaintenanceJob.objects.get_or_create(
                    plan=pm,
                    due_date=month_start + timedelta(days=(idx % 25) + 1),
                    defaults={
                        "order": wo,
                        "asset": ast,
                        "cost_center": ccs["CC-DXB-ENG"],
                        "assignee": staff_users["omar.eng"] if idx % 2 == 0 else staff_users["hassan.qa"],
                        "stage": "completed" if idx <= 4 else "in_progress" if idx <= 9 else "waiting",
                        "checklist": pm.checklist,
                        "started_at": now - timedelta(hours=idx * 2) if idx <= 9 else None,
                        "completed_at": now - timedelta(hours=idx) if idx <= 4 else None,
                    },
                )

        # 11. HR: 12 Employees, Compensation, Timesheets, Leaves & 2 Months Payroll Runs
        dept_eng, _ = HRReference.objects.get_or_create(kind="department", code="DEPT-ENG", defaults={"name": "Engineering & Automation"})
        dept_ops, _ = HRReference.objects.get_or_create(kind="department", code="DEPT-OPS", defaults={"name": "Operations & Supply Chain"})
        dept_fin, _ = HRReference.objects.get_or_create(kind="department", code="DEPT-FIN", defaults={"name": "Finance & Administration"})
        dept_sls, _ = HRReference.objects.get_or_create(kind="department", code="DEPT-SLS", defaults={"name": "Commercial & Customer Success"})

        title_lead, _ = HRReference.objects.get_or_create(kind="title", code="TTL-LEAD", defaults={"name": "Lead Control Systems Engineer"})
        title_mgr, _ = HRReference.objects.get_or_create(kind="title", code="TTL-MGR", defaults={"name": "Operations Manager"})
        title_acc, _ = HRReference.objects.get_or_create(kind="title", code="TTL-ACC", defaults={"name": "Senior Accountant"})
        title_spec, _ = HRReference.objects.get_or_create(kind="title", code="TTL-SPEC", defaults={"name": "Field & Systems Specialist"})

        grade_g4, _ = HRReference.objects.get_or_create(kind="grade", code="GRD-04", defaults={"name": "Grade 4 — Specialist"})
        grade_g5, _ = HRReference.objects.get_or_create(kind="grade", code="GRD-05", defaults={"name": "Grade 5 — Senior Specialist"})
        grade_g6, _ = HRReference.objects.get_or_create(kind="grade", code="GRD-06", defaults={"name": "Grade 6 — Management"})

        emp_roster = [
            ("EMP-10001", "Ahmed Salama", dxb, dept_eng, title_lead, grade_g6, admin, ccs["CC-DXB-ENG"], Decimal("19000.00"), Decimal("7500.00")),
            ("EMP-10002", "Sara Al Mansoori", dxb, dept_ops, title_mgr, grade_g6, staff_users["sara.ops"], ccs["CC-DXB-OPS"], Decimal("17000.00"), Decimal("6500.00")),
            ("EMP-10003", "Omar Al Hashimi", auh, dept_eng, title_lead, grade_g5, staff_users["omar.eng"], ccs["CC-AUH-OPS"], Decimal("14500.00"), Decimal("5500.00")),
            ("EMP-10004", "Layla Kareem", dxb, dept_fin, title_acc, grade_g5, staff_users["layla.fin"], ccs["CC-DXB-OPS"], Decimal("13500.00"), Decimal("4500.00")),
            ("EMP-10005", "Khaled Al Nuaimi", shj, dept_ops, title_mgr, grade_g6, staff_users["khaled.sup"], ccs["CC-SHJ-MFG"], Decimal("15500.00"), Decimal("5500.00")),
            ("EMP-10006", "Fatima Al Suwaidi", dxb, dept_fin, title_mgr, grade_g6, staff_users["fatima.hr"], ccs["CC-DXB-OPS"], Decimal("15000.00"), Decimal("5000.00")),
            ("EMP-10007", "Tariq Al Shamsi", shj, dept_eng, title_lead, grade_g5, staff_users["tariq.mfg"], ccs["CC-SHJ-MFG"], Decimal("14000.00"), Decimal("5000.00")),
            ("EMP-10008", "Nadia Al Ketbi", dxb, dept_sls, title_mgr, grade_g5, staff_users["nadia.crm"], ccs["CC-DXB-OPS"], Decimal("13800.00"), Decimal("4800.00")),
            ("EMP-10009", "Youssef Mahmoud", auh, dept_eng, title_spec, grade_g5, staff_users["youssef.pm"], ccs["CC-AUH-OPS"], Decimal("13200.00"), Decimal("4500.00")),
            ("EMP-10010", "Mariam Al Mazrouei", dxb, dept_sls, title_spec, grade_g4, staff_users["mariam.hd"], ccs["CC-DXB-OPS"], Decimal("11500.00"), Decimal("3800.00")),
            ("EMP-10011", "Hassan Al Blooshi", rak, dept_eng, title_spec, grade_g4, staff_users["hassan.qa"], ccs["CC-RAK-DEP"], Decimal("12000.00"), Decimal("4000.00")),
            ("EMP-10012", "Reem Al Falasi", dxb, dept_sls, title_spec, grade_g4, staff_users["reem.pub"], ccs["CC-DXB-OPS"], Decimal("11800.00"), Decimal("3900.00")),
        ]
        employees = []
        for num, name, st, dept, ttl, grd, usr, cc, basic, allow in emp_roster:
            emp, _ = Employee.objects.get_or_create(
                number=num,
                defaults={
                    "name": name,
                    "site": st,
                    "department": dept.name,
                    "title": ttl.name,
                    "joined": date(2024, 1, 15),
                    "user": usr if not Employee.objects.filter(user=usr).exists() else None,
                    "nationality": "UAE / Egypt",
                    "department_ref": dept,
                    "title_ref": ttl,
                    "grade_ref": grd,
                },
            )
            Compensation.objects.get_or_create(
                employee=emp,
                effective=date(2026, 1, 1),
                defaults={"center": cc, "basic": basic, "allowance": allow, "actor": admin},
            )
            employees.append((emp, cc, basic, allow))

        for st in site_list:
            AttendancePolicy.objects.get_or_create(site=st, defaults={"week_start": 0, "standard_minutes": 480})

        wk_start = today - timedelta(days=today.weekday())
        ws_dt = timezone.make_aware(datetime.combine(wk_start, time(8, 0)))
        we_dt = ws_dt + timedelta(days=5)
        for idx, (emp, _, _, _) in enumerate(employees[:6]):
            WeeklyTimesheet.objects.get_or_create(
                employee=emp,
                starts_at=ws_dt,
                defaults={
                    "site": emp.site,
                    "week_start": wk_start,
                    "ends_at": we_dt,
                    "timezone": "Asia/Dubai",
                    "standard_minutes": 480,
                    "state": "approved" if idx < 4 else "submitted",
                    "snapshot": {"mon": 480, "tue": 510, "wed": 480, "thu": 480, "fri": 450},
                    "submitted_by": emp.user or admin,
                    "reviewed_by": admin if idx < 4 else None,
                    "reviewed_at": now if idx < 4 else None,
                },
            )

        for idx, (emp, _, _, _) in enumerate(employees[1:6], start=1):
            Leave.objects.get_or_create(
                employee=emp,
                site=emp.site,
                start=today + timedelta(days=idx * 6),
                end=today + timedelta(days=idx * 6 + 2),
                defaults={
                    "paid": True,
                    "reason": f"Scheduled annual leave #{idx} — إجازة سنوية",
                    "state": "approved" if idx <= 2 else "pending",
                    "requested_by": emp.user or admin,
                },
            )

        for st in (dxb, auh, shj):
            prun, _ = PayrollRun.objects.get_or_create(
                site=st,
                month=prev_start,
                defaults={
                    "state": "approved",
                    "prepared_by": staff_users["layla.fin"],
                    "approved_by": admin,
                    "review_note": f"Approved monthly payroll for {st.name}",
                },
            )
            for emp, cc, basic, allow in employees:
                if emp.site_id == st.pk:
                    gross = basic + allow
                    Payslip.objects.get_or_create(
                        run=prun,
                        employee=emp,
                        defaults={
                            "month": prev_start,
                            "center": cc,
                            "snapshot": {"basic": str(basic), "allowance": str(allow)},
                            "gross": gross,
                            "deduction": Decimal("0.00"),
                            "net": gross,
                            "reserved": True,
                        },
                    )

        # 12. Projects (6 Projects), 24 Tasks & 8 Expense Claims
        proj_specs = [
            ("PRJ-DXB-2026-01", dxb, "Jebel Ali Cold-Chain SCADA & PLC Automation", "active"),
            ("PRJ-DXB-2026-02", dxb, "Downtown District Cooling Chiller Optimization", "active"),
            ("PRJ-AUH-2026-01", auh, "Taweelah RO Desalination Telemetry Upgrade", "active"),
            ("PRJ-AUH-2026-02", auh, "Ruwais Pipeline Valve Actuator Retrofit", "draft"),
            ("PRJ-SHJ-2026-01", shj, "SAIF Zone Automated Warehouse Conveyors", "active"),
            ("PRJ-RAK-2026-01", rak, "Al Ghail Kiln Burner PLC Migration", "active"),
        ]
        projects = []
        for pcode, st, pname, pstate in proj_specs:
            prj, _ = Project.objects.get_or_create(
                site=st,
                code=pcode,
                defaults={"name": pname, "start": prev2_start, "end": today + timedelta(days=75), "state": pstate},
            )
            mem_mgr, _ = Membership.objects.get_or_create(project=prj, user=admin, defaults={"role": "manager", "active": True})
            mem_eng, _ = Membership.objects.get_or_create(project=prj, user=staff_users["omar.eng"], defaults={"role": "engineer", "active": True})
            LabourRate.objects.get_or_create(project=prj, role="manager", defaults={"rate": Decimal("350.00")})
            LabourRate.objects.get_or_create(project=prj, role="engineer", defaults={"rate": Decimal("240.00")})
            Baseline.objects.get_or_create(
                project=prj,
                version=1,
                defaults={
                    "approved_by": admin,
                    "reason": "Approved baseline budget",
                    "categories": {"engineering": "95000.00", "hardware": "160000.00"},
                    "hours": Decimal("480.00"),
                },
            )
            for t_idx, (ttitle, hrs, tstate, ms) in enumerate([
                ("P&ID Engineering & Control Narrative", Decimal("48.00"), "done", False),
                ("Panel Assembly & Factory Acceptance Test (FAT)", Decimal("72.00"), "in_progress", False),
                ("Field Loop Calibration & Commissioning", Decimal("64.00"), "todo", False),
                ("Site Acceptance Test (SAT) & Handover Sign-Off", Decimal("24.00"), "todo", True),
            ], start=1):
                Task.objects.get_or_create(
                    project=prj,
                    title=ttitle,
                    defaults={
                        "owner": mem_mgr if ms else mem_eng,
                        "due": today + timedelta(days=t_idx * 12),
                        "planned_hours": hrs,
                        "state": tstate,
                        "milestone": ms,
                    },
                )
            projects.append(prj)

        for idx, prj in enumerate(projects, start=1):
            emp_obj = employees[idx % len(employees)][0]
            if not ExpenseClaim.objects.filter(employee=emp_obj, project=prj).exists():
                net_amt = Decimal(str(850 + idx * 220))
                tax_amt = (net_amt * Decimal("0.05")).quantize(Decimal("0.01"))
                claim = ExpenseClaim.objects.create(
                    employee=emp_obj,
                    site=prj.site,
                    project=prj,
                    cost_center=ccs["CC-DXB-ENG"],
                    creator=emp_obj.user or admin,
                    currency="AED",
                    method="out_of_pocket",
                    status="approved" if idx <= 3 else "submitted",
                    gross=net_amt + tax_amt,
                    review_note=f"Field commissioning travel & consumables for {prj.code}",
                )
                ExpenseLine.objects.create(
                    claim=claim,
                    category="travel",
                    date=today - timedelta(days=idx * 2),
                    description=f"Site travel, Salik tolls & calibration fittings ({prj.code})",
                    net=net_amt,
                    tax=tax_amt,
                    recoverable=True,
                )

        # 13. CRM: 5 Stages, 24 Leads, 18 Opportunities & 32 Activities
        stage_specs = [
            ("new_inquiry", "New Inquiry", "استفسار جديد", 10, "open"),
            ("technical_prop", "Technical Proposal", "عرض فني ومالي", 20, "open"),
            ("negotiation", "Commercial Negotiation", "تفاوض تجاري", 30, "open"),
            ("closed_won", "Closed Won", "صفقة رابحة", 40, "won"),
            ("closed_lost", "Closed Lost", "صفقة مفقودة", 50, "lost"),
        ]
        stages = {}
        for code, nen, nar, order, kind in stage_specs:
            st_obj = Stage.objects.filter(kind=kind).first() if kind in ("won", "lost") else Stage.objects.filter(code=code).first()
            if st_obj is None:
                st_obj = Stage.objects.create(code=code, name_en=nen, name_ar=nar, order=order, kind=kind, active=True)
            stages[code] = st_obj

        open_stage_keys = ["new_inquiry", "technical_prop", "negotiation", "closed_won", "closed_lost"]
        lead_names = [
            ("Eng. Tariq Al Nasser", "Gulf Smart Buildings & MEP", "Dubai Automation Expo"),
            ("Mona Al Zaabi", "Emirates Energy & Water Systems", "Direct Referral"),
            ("Rami Haddad", "DP World Cold Chain Terminals", "Website Inquiry"),
            ("Saeed Al Maktoum", "Emaar District Cooling Utilities", "Partner Network"),
            ("Huda Al Qasimi", "Sharjah Oasis Bottling Plant", "LinkedIn Campaign"),
            ("Waleed Mansour", "Al Ain Dairy & Food Processing", "Trade Show"),
            ("Karim Abdelrahman", "RAK Ceramics & Kiln Automation", "Direct Call"),
            ("Zayed Al Dhaheri", "Taweelah Desalination & Power Co.", "Tender Portal"),
            ("Dina Al Hashmi", "Dubai Metro Rail Systems Maintenance", "Existing Client"),
            ("Majid Al Suwaidi", "Fujairah Bunkering & Tank Terminals", "Website Inquiry"),
            ("Lina Khoury", "Al Habtoor Hospitality Engineering", "Referral"),
            ("Faisal Al Marri", "Ruwais Petrochemical Logistics", "ADIPEC Exhibition"),
            ("Noura Al Kaabi", "Julphar Pharmaceutical Cleanrooms", "Outbound Sales"),
            ("Samir El-Masry", "Transguard Facility Automation", "Partner Network"),
            ("Amina Al Shamsi", "Sharjah Maritime & Freezone Cargo", "Website Inquiry"),
            ("Bader Al Blooshi", "Al Futtaim Industrial Logistics LLC", "Account Expansion"),
            ("Salma Al Nuaimi", "Mussafah Steel & Rolling Mills", "Cold Outreach"),
            ("Adel Ghobash", "JAFZA Cold Storage Logistics", "Dubai Automation Expo"),
        ]
        for idx, (lname, comp, src) in enumerate(lead_names, start=1):
            st = site_list[idx % len(site_list)]
            cust = customers[idx % len(customers)]
            owner = admin if idx % 3 == 0 else staff_users["nadia.crm"]
            email_addr = f"lead{idx:02d}@erpipro-prospect.demo"
            lead, _ = Lead.objects.get_or_create(
                site=st,
                email=email_addr,
                defaults={
                    "party": cust.party,
                    "owner": owner,
                    "name": lname,
                    "company": comp,
                    "phone": f"+971-50-{4100000 + idx * 137}",
                    "source": src,
                    "state": "converted" if idx <= 14 else "qualified" if idx <= 16 else "new",
                },
            )
            if idx <= 14:
                st_key = open_stage_keys[idx % len(open_stage_keys)]
                opp, created_opp = Opportunity.objects.get_or_create(
                    site=st,
                    title=f"Turnkey PLC/SCADA & Instrumentation Package #{idx:02d} — {comp}",
                    defaults={
                        "owner": owner,
                        "lead": lead,
                        "customer": cust,
                        "stage": stages[st_key],
                        "estimated_amount": Decimal(str(65000 + idx * 18500)),
                        "currency": "AED",
                        "expected_close": today + timedelta(days=idx * 4),
                    },
                )
                if created_opp:
                    StageHistory.objects.create(
                        opportunity=opp,
                        previous_stage=stages["new_inquiry"],
                        stage=stages[st_key],
                        previous_names={"en": "New Inquiry", "ar": "استفسار جديد"},
                        stage_names={"en": stages[st_key].name_en, "ar": stages[st_key].name_ar},
                        actor=owner,
                        reason="Technical scoping and commercial BOQ review",
                    )
                    CrmActivity.objects.create(
                        site=st,
                        owner=owner,
                        opportunity=opp,
                        kind="meeting" if idx % 2 == 0 else "call",
                        title=f"Technical & Commercial Review — {comp}",
                        notes="Discuss delivery milestones, FAT schedule, and payment terms.",
                        due_at=now + timedelta(days=idx, hours=2),
                        state="open" if idx % 3 != 0 else "done",
                    )

        # 14. Helpdesk: Categories, SLAs & 24 Support Tickets
        ticket_subjects = [
            ("Modbus TCP Gateway Intermittent Timeout on Line 2", "high", "in_progress"),
            ("HART 475 Communicator Device Descriptor Update Request", "medium", "open"),
            ("SCADA Historian SQL Archive Disk Threshold Alert", "high", "open"),
            ("VFDs Harmonic Filter Commissioning Support — RAK Kiln", "urgent", "in_progress"),
            ("Calibration Certificate Renewal for 12x Rosemount 3051S", "low", "resolved"),
            ("HMI Alarm Banner Arabic RTL Font Alignment Check", "medium", "resolved"),
            ("AUMA MOV Actuator Torque Switch Trip Investigation", "high", "open"),
            ("VPN Tunnel Failover Test — Abu Dhabi Branch", "medium", "closed"),
            ("Request Spare 24V SITOP Power Supply Dispatch", "low", "open"),
            ("Profibus DP Repeater Diagnostics on Packaging Line", "high", "in_progress"),
            ("Annual Preventive Maintenance Schedule Confirmation", "low", "resolved"),
            ("Chiller Plant Flowmeter Pulse Output Scaling", "medium", "open"),
        ]
        for idx, (subj, prio, tstatus) in enumerate(ticket_subjects, start=1):
            st = site_list[idx % len(site_list)]
            cat, _ = TicketCategory.objects.get_or_create(
                site=st,
                code="field-support",
                defaults={"name_ar": "الدعم الفني الميداني", "name_en": "Field & Telemetry Support"},
            )
            sla, _ = SLAPolicy.objects.get_or_create(
                site=st,
                priority=prio,
                version=1,
                defaults={"first_response_minutes": 60, "resolution_minutes": 480},
            )
            tkt, created_tkt = Ticket.objects.get_or_create(
                site=st,
                subject=subj,
                defaults={
                    "requester": staff_users["mariam.hd"],
                    "assignee": staff_users["omar.eng"] if idx % 2 == 0 else staff_users["hassan.qa"],
                    "category": cat,
                    "policy": sla,
                    "priority": prio,
                    "status": tstatus,
                    "description": f"Detailed engineering support request #{idx:02d}: {subj}.",
                    "created_at": now - timedelta(hours=idx * 4),
                    "first_response_due": now - timedelta(hours=idx * 3),
                    "resolution_due": now + timedelta(hours=idx * 3),
                    "first_response_at": now - timedelta(hours=idx * 3, minutes=15),
                    "resolved_at": now - timedelta(hours=1) if tstatus in ("resolved", "closed") else None,
                    "closed_at": now if tstatus == "closed" else None,
                },
            )
            if created_tkt:
                TicketComment.objects.create(
                    ticket=tkt,
                    author=staff_users["omar.eng"],
                    body="Initial diagnostics completed; field engineer assigned with calibrated test kit.",
                    internal=False,
                )

        # 15. Scheduling: 6 Resources (Internal + Public Portal Scoped) & 18 Appointments
        res_specs = [
            ("Commissioning & Calibration Engineering Bay #1", dxb, sp_gulf),
            ("FAT Panel Testing & Simulation Lab — Dubai", dxb, sp_gulf),
            ("Mussafah Pressure & Flow Calibration Bench", auh, sp_auh),
            ("Abu Dhabi Field Engineer Consultation Slot", auh, sp_auh),
            ("Sharjah Logistics & Dispatch Dock #1", shj, sp_shj),
            ("RAK Heavy Equipment Inspection Bay", rak, None),
        ]
        resources = []
        for rname, st, sp in res_specs:
            res, _ = Resource.objects.get_or_create(
                site=st,
                name=rname,
                defaults={
                    "space": sp,
                    "timezone": "Asia/Dubai",
                    "weekdays": [0, 1, 2, 3, 4],
                    "opens": time(8, 0),
                    "closes": time(17, 0),
                    "slot_minutes": 60,
                },
            )
            if sp and res.space_id != sp.pk:
                res.space = sp
                res.save(update_fields=["space"])
            resources.append(res)

        portal_party_list = [parties["P-PORTAL-01"], parties["P-PORTAL-02"], parties["P-PORTAL-03"], parties["P-PORTAL-04"], parties["P-PORTAL-05"], parties["P-PORTAL-06"]]
        for idx in range(1, 15):
            res = resources[idx % len(resources)]
            pty = portal_party_list[idx % len(portal_party_list)] if res.space else parties["P-CUST-09"]
            appt_dt = timezone.make_aware(datetime.combine(today + timedelta(days=(idx % 10) + 1), time(9 + (idx % 6), 0)))
            Appointment.objects.get_or_create(
                site=res.site,
                resource=res,
                party=pty,
                start=appt_dt,
                defaults={"space": res.space, "end": appt_dt + timedelta(hours=1), "status": "scheduled"},
            )

        # 16. Subscriptions & Billing Core (4 Plans, 14 Contracts, Paid & Open Invoices/Receipts)
        for st in site_list:
            DunningPolicy.objects.get_or_create(site=st, defaults={"first": 3, "second": 7, "final": 14, "suspend": 21})
            SubscriptionGLMapping.objects.get_or_create(
                site=st,
                defaults={
                    "receivable": accts["1300"],
                    "deferred": accts["2400"],
                    "revenue": accts["4300"],
                    "setup": accts["4200"],
                    "vat": accts["2300"],
                    "bank": accts["1110"],
                },
            )

        plan_defs = [
            ("PLAN-SCADA-PRO", "24/7 Telemetry & Preventive SLA Plan", "باقة المراقبة الذكية والصيانة الوقائية", dxb, Decimal("4500.00"), Decimal("1500.00")),
            ("PLAN-CAL-ANNUAL", "Quarterly HART & Flow Calibration Retainer", "باقة المعايرة الدورية للأجهزة الصناعية", auh, Decimal("6800.00"), Decimal("1000.00")),
            ("PLAN-CLOUD-ERP", "Dedicated Cloud SCADA & Historian Hosting", "استضافة سحابية مخصصة لأنظمة SCADA", dxb, Decimal("2900.00"), Decimal("500.00")),
            ("PLAN-FLEET-LOG", "Sharjah Cold-Chain Sensor Monitoring SLA", "مراقبة مستشعرات التبريد وسلسلة الإمداد", shj, Decimal("3600.00"), Decimal("800.00")),
        ]
        plan_versions = []
        for pcode, nen, nar, st, price, setup in plan_defs:
            pl, _ = Plan.objects.get_or_create(site=st, code=pcode, defaults={"name_en": nen, "name_ar": nar, "active": True})
            pv, _ = PlanVersion.objects.get_or_create(
                plan=pl,
                version=1,
                defaults={"currency": "AED", "interval": "monthly", "days": 30, "price": price, "setup": setup, "tax": Decimal("0.050"), "trial_days": 0},
            )
            plan_versions.append(pv)

        for idx, pty in enumerate(portal_party_list + [parties["P-CUST-07"], parties["P-CUST-08"], parties["P-CUST-09"], parties["P-CUST-10"]], start=1):
            pv = plan_versions[idx % len(plan_versions)]
            st = pty.site
            contract, _ = Contract.objects.get_or_create(
                site=st,
                party=pty,
                version=pv,
                defaults={
                    "space": pty.space,
                    "renewal_version": pv,
                    "start": month_start,
                    "next_date": month_end + timedelta(days=1),
                    "anchor": month_start,
                    "zone": "Asia/Dubai",
                    "status": "active",
                },
            )
            tax_amt = (pv.price * pv.tax).quantize(Decimal("0.01"))
            tot_amt = pv.price + tax_amt
            is_paid = idx % 3 != 0
            inv_num = f"SUB-INV-2026-{idx:04d}"
            existing_inv = BillingInvoice.objects.filter(site=st, number=inv_num).first()
            if existing_inv and Cycle.objects.filter(invoice=existing_inv).exclude(contract=contract, start=month_start).exists():
                inv_num = f"SUB-INV-2026-{contract.pk:04d}-{idx:02d}"
            binv, created_binv = BillingInvoice.objects.get_or_create(
                site=st,
                number=inv_num,
                defaults={
                    "space": pty.space,
                    "party": pty,
                    "currency": "AED",
                    "status": "paid" if is_paid else "issued",
                    "subtotal": pv.price,
                    "tax": tax_amt,
                    "total": tot_amt,
                    "paid": tot_amt if is_paid else Decimal("0.00"),
                },
            )
            if pty.space and binv.space_id != pty.space_id:
                binv.space = pty.space
                binv.save(update_fields=["space"])
            if created_binv:
                BillingLine.objects.create(
                    invoice=binv,
                    description=f"{pv.plan.name_en} — Monthly Billing Cycle",
                    quantity=Decimal("1.000"),
                    unit_price=pv.price,
                    tax_rate=pv.tax,
                    line_total=tot_amt,
                )
                if is_paid:
                    BillingReceipt.objects.create(
                        site=st,
                        space=pty.space,
                        invoice=binv,
                        number=f"RCP-2026-{idx:04d}",
                        amount=tot_amt,
                    )
            Cycle.objects.get_or_create(
                contract=contract,
                start=month_start,
                defaults={"version": pv, "end": month_end, "due": month_start + timedelta(days=5), "invoice": binv},
            )

        # 17. Manufacturing: 3 BOMs & 10 Production Orders
        bom_specs = [
            ("INV-PNL-SCADA", [("INV-PLC-1500", "1"), ("INV-HMI-1200", "1"), ("INV-PSU-24V", "2"), ("INV-SW-SCAL", "1"), ("INV-ENC-IP66", "1")]),
            ("INV-PNL-MCC", [("INV-VFD-15KW", "2"), ("INV-PLC-1200", "1"), ("INV-PSU-24V", "1"), ("INV-ENC-IP66", "1")]),
            ("INV-PNL-BMS", [("INV-PLC-1200", "1"), ("INV-IO-ET200", "2"), ("INV-HMI-0700", "1"), ("INV-GTW-MOD", "1")]),
        ]
        boms = []
        for fg_code, comp_list in bom_specs:
            bom, _ = Bill.objects.get_or_create(
                site=dxb,
                item=items[fg_code],
                version=1,
                defaults={
                    "effective": prev2_start,
                    "quantity": Decimal("1"),
                    "components": [{"item_code": c, "quantity": q} for c, q in comp_list],
                    "state": "released",
                },
            )
            MfgPolicy.objects.get_or_create(
                site=dxb,
                item=items[fg_code],
                defaults={"method": "make", "lead_days": 5, "source": locs["WH-DXB-MFG:RM-01"][1], "destination": locs["WH-DXB-MFG:FG-01"][1]},
            )
            boms.append((bom, comp_list))

        for idx in range(1, 10):
            bom, comp_list = boms[idx % len(boms)]
            qty = Decimal(str(1 + (idx % 3)))
            mo, created_mo = MfgOrder.objects.get_or_create(
                site=dxb,
                item=bom.item,
                due=today + timedelta(days=idx * 4),
                defaults={
                    "source": locs["WH-DXB-MFG:RM-01"][1],
                    "destination": locs["WH-DXB-MFG:FG-01"][1],
                    "bill": bom,
                    "quantity": qty,
                    "state": "released" if idx <= 6 else "draft",
                },
            )
            if created_mo:
                for ccode, per_u in comp_list:
                    Material.objects.create(
                        order=mo,
                        item=items[ccode],
                        source=locs["WH-DXB-MFG:RM-01"][1],
                        required=(qty * Decimal(per_u)),
                        snapshot={"code": ccode, "per_unit": per_u},
                    )

        # 18. Supply: 10 Inter-Site Stock Transfers
        transfer_routes = [
            (dxb, auh, "INV-DPT-3051", locs["WH-DXB-01:A-01"][1], locs["WH-AUH-01:S-01"][1], Decimal("4"), "approved"),
            (dxb, shj, "INV-PLC-1200", locs["WH-DXB-01:B-01"][1], locs["WH-SHJ-01:L-02"][1], Decimal("6"), "approved"),
            (shj, dxb, "INV-CBL-SHLD", locs["WH-SHJ-01:L-01"][1], locs["WH-DXB-MFG:RM-01"][1], Decimal("400"), "approved"),
            (dxb, rak, "INV-TT-644", locs["WH-DXB-01:A-01"][1], locs["WH-RAK-01:R-02"][1], Decimal("8"), "draft"),
            (auh, dxb, "INV-CAL-LOOP", locs["WH-AUH-01:S-01"][1], locs["WH-DXB-01:A-01"][1], Decimal("2"), "approved"),
            (shj, rak, "INV-CBL-PROF", locs["WH-SHJ-01:L-01"][1], locs["WH-RAK-01:R-01"][1], Decimal("250"), "draft"),
        ]
        for idx, (src_st, dst_st, icode, src_loc, dst_loc, qty, tstate) in enumerate(transfer_routes, start=1):
            note = f"Inter-branch replenishment #{idx:02d}: {icode} ({src_st.name.split('—')[0].strip()} -> {dst_st.name.split('—')[0].strip()})"
            tr, created_tr = Transfer.objects.get_or_create(
                site=src_st,
                destination=dst_st,
                note=note,
                defaults={"state": tstate},
            )
            if created_tr:
                TransferLine.objects.create(
                    transfer=tr,
                    item=items[icode],
                    source=src_loc,
                    destination=dst_loc,
                    quantity=qty,
                    dispatched=Decimal("0"),
                )

        # 19. Bank Reconciliation: 2 Bank Profiles, Statements & 16 Statement Lines
        for st, bank_acc, bname, last4, open_bal in [
            (dxb, accts["1110"], "Emirates NBD Corporate AED", "8841", Decimal("1250000.00")),
            (auh, accts["1120"], "ADCB Abu Dhabi Operations AED", "3390", Decimal("450000.00")),
        ]:
            bprof, _ = BankProfile.objects.get_or_create(
                site=st,
                account=bank_acc,
                defaults={
                    "charges": accts["5500"],
                    "interest": accts["4100"],
                    "name": bname,
                    "last_four": last4,
                    "currency": "AED",
                    "tolerance": Decimal("0.05"),
                    "last_balance": open_bal,
                    "last_date": prev_end,
                },
            )
            stmt = BankStatement.objects.filter(profile=bprof).first()
            if stmt is None:
                running = open_bal
                txns = [
                    (month_start + timedelta(days=2), "FTS INWARD — AL FUTTAIM INDUSTRIAL LOGISTICS", "FT263110094821", Decimal("89250.00")),
                    (month_start + timedelta(days=5), "SWEEP TRANSFER — SCADA SUBSCRIPTION SETTLEMENT", "FT263110095104", Decimal("31500.00")),
                    (month_start + timedelta(days=7), "OUTWARD WIRE — SIEMENS MIDDLE EAST AUTOMATION", "FT263110095889", Decimal("-31000.00")),
                    (month_start + timedelta(days=10), "DEWA / UTILITY AUTOPAY SETTLEMENT", "DD263110096210", Decimal("-4850.00")),
                    (month_start + timedelta(days=12), "FTS INWARD — EMIRATES ENERGY & WATER SYSTEMS", "FT263110096992", Decimal("47040.00")),
                    (month_start + timedelta(days=14), "MONTHLY CORPORATE ACCOUNT SERVICE FEE", "CHG263110097100", Decimal("-125.00")),
                ]
                end_bal = running + sum(t[3] for t in txns)
                stmt = BankStatement.objects.create(
                    profile=bprof,
                    start=month_start,
                    end=month_end,
                    opening_balance=open_bal,
                    statement_ending_balance=end_bal,
                    status="draft",
                )
                for tdate, tdesc, tref, tamt in txns:
                    running += tamt
                    BankStatementLine.objects.create(
                        statement=stmt,
                        date=tdate,
                        description=tdesc,
                        reference=tref,
                        amount=tamt,
                        running_balance=running,
                    )

        # 20. Marketing: Providers, 16 Contacts, 3 Segments, 3 Templates & 4 Campaigns
        for st in (dxb, auh):
            MktSiteSettings.objects.get_or_create(site=st, defaults={"consent_mode": "single"})
        prov_email, _ = ProviderAccount.objects.get_or_create(
            site=dxb,
            channel="email",
            defaults={"adapter": "smtp", "sender": "updates@erpipro.demo", "rate": 20, "enabled": True},
        )
        seg_ind, _ = Segment.objects.get_or_create(site=dxb, name="UAE Industrial & MEP Decision Makers")
        seg_sla, _ = Segment.objects.get_or_create(site=dxb, name="Active SCADA & Calibration SLA Clients")

        for idx, cust in enumerate(customers[:14], start=1):
            addr = cust.party.email or f"contact{idx:02d}@erpipro.demo"
            ep_key = hashlib.sha256(addr.encode()).hexdigest()
            mc, _ = Contact.objects.get_or_create(
                site=dxb,
                source_key=f"northwind-contact-{idx:02d}",
                defaults={"first_name": cust.name.split()[0], "customer": cust},
            )
            ep, _ = ContactEndpoint.objects.get_or_create(
                contact=mc,
                channel="email",
                defaults={"address": addr, "endpoint_key": ep_key},
            )
            Consent.objects.get_or_create(endpoint=ep, purpose="marketing", defaults={"status": "granted"})
            seg_ind.contacts.add(mc)
            if idx <= 7:
                seg_sla.contacts.add(mc)

        for cname, subj, body_txt, cstatus, seg_target in [
            ("UAE Industrial Reliability Drive 2026", "Upgrade Your Plant Telemetry & Preventive SLA with ERPi Pro",
             "Hello {{first_name}}, discover turnkey S7-1500 PLC, SCADA, and HART calibration packages across Dubai & Abu Dhabi.", "scheduled", seg_ind),
            ("Annual Calibration & Shutdown Readiness", "Book Your Q4 Preventive Calibration Slot in Dubai or Mussafah",
             "Hello {{first_name}}, reserve your priority engineering bay online through our customer portal.", "draft", seg_sla),
        ]:
            tpl, _ = MktTemplate.objects.get_or_create(site=dxb, name=cname, channel="email")
            ver, _ = TemplateVersion.objects.get_or_create(
                template=tpl,
                number=1,
                defaults={"subject": subj, "body": body_txt, "locked_at": now},
            )
            camp, _ = Campaign.objects.get_or_create(
                site=dxb,
                title=cname,
                defaults={
                    "template_version": ver,
                    "channel": "email",
                    "purpose": "marketing",
                    "creator": admin,
                    "approver": admin,
                    "provider": prov_email,
                    "status": cstatus,
                    "scheduled_at": now + timedelta(days=4) if cstatus == "scheduled" else None,
                    "schedule_timezone": "Asia/Dubai",
                },
            )
            camp.segments.add(seg_target)

        # 21. Public Sites Content (Pages, Posts, Menus, Revisions, Comments) & Verified Portal Accounts
        portal_pw_hash = make_password("PortalDemo!2026")
        portal_users_specs = [
            ("customer1@erpipro.demo", "Al Futtaim Industrial Portal Admin", parties["P-PORTAL-01"], sp_gulf),
            ("customer2@erpipro.demo", "DP World Cold Chain Operations", parties["P-PORTAL-02"], sp_gulf),
            ("customer3@erpipro.demo", "Emaar District Cooling Engineering", parties["P-PORTAL-03"], sp_gulf),
            ("auh.client1@erpipro.demo", "Emirates Energy & Water Portal", parties["P-PORTAL-04"], sp_auh),
            ("auh.client2@erpipro.demo", "Taweelah Desalination Maintenance", parties["P-PORTAL-05"], sp_auh),
            ("shj.client1@erpipro.demo", "Sharjah Maritime Cargo Portal", parties["P-PORTAL-06"], sp_shj),
        ]
        portal_identities = []
        for p_email, p_disp, p_party, p_space in portal_users_specs:
            ident, _ = ExternalIdentity.objects.get_or_create(
                normalized_email=p_email,
                defaults={
                    "party": p_party,
                    "password_hash": portal_pw_hash,
                    "display_name": p_disp,
                    "status": "active",
                    "verified_at": now - timedelta(days=10),
                },
            )
            if ident.party_id != p_party.pk:
                ident.party = p_party
                ident.save(update_fields=["party"])
            ExternalMembership.objects.get_or_create(identity=ident, space=p_space, defaults={"active": True})
            portal_identities.append((ident, p_space))

        def make_doc(title, summary, heading, p1, bullets, quote_txt):
            return {
                "title": title,
                "summary": summary,
                "body_blocks": [
                    {"type": "heading", "text": heading},
                    {"type": "paragraph", "text": p1},
                    {"type": "list", "items": bullets},
                    {"type": "quote", "text": quote_txt},
                ],
                "cover_media_id": None,
                "seo_meta": {
                    "title": title,
                    "description": summary,
                    "robots": "index,follow",
                    "og_image_media": None,
                },
            }

        cms_catalog = [
            # Gulf Portal (Dubai)
            (sp_gulf, "page", "welcome-erpi-pro", "Welcome to ERPi Pro Gulf Industrial Portal",
             "Turnkey PLC/SCADA automation, HART calibration, and 24/7 customer self-service in Dubai & the UAE.",
             "Enterprise Automation & Customer Self-Service",
             "Sign in to your customer account at /public/account to view invoices, download official receipts, and book calibration bays.",
             ["24/7 SCADA & Telemetry Support", "ISO-Traceable HART & Pressure Calibration", "Instant Online Invoice & Receipt Access"],
             "Engineered for high-reliability industrial operations across the Gulf.",
             "مرحبًا بكم في بوابة إي آر بي آي برو الخليج الصناعية",
             "حلول أتمتة المصانع وأنظمة SCADA ومعايرة الأجهزة الدقيقة مع بوابة خدمة ذاتية متكاملة في دبي والإمارات.",
             "الأتمتة الصناعية والخدمة الذاتية للعملاء",
             "سجّل الدخول إلى حسابك عبر /public/account لاستعراض الفواتير وتحميل الإيصالات وحجز مواعيد المعايرة والاختبار.",
             ["دعم فني على مدار الساعة لأنظمة SCADA", "معايرة معتمدة لأجهزة القياس والتحكم", "استعراض الفواتير والإيصالات إلكترونيًا"],
             "مصممة لأعلى معايير الاعتمادية التشغيلية في المنطقة."),
            (sp_gulf, "page", "services-and-sla", "Industrial Automation & Calibration Services",
             "Explore our engineering capabilities across Siemens S7-1500 PLCs, AUMA MOV actuators, and predictive maintenance.",
             "Core Engineering Capabilities",
             "Our Jebel Ali and Mussafah teams deliver end-to-end panel fabrication, FAT/SAT commissioning, and annual SLA contracts.",
             ["Custom RTU & SCADA Control Cabinets", "Motor-Operated Valve (MOV) Overhaul & Torque Testing", "24/7 Remote Telemetry Monitoring"],
             "Zero unplanned downtime through preventive engineering.",
             "خدمات الأتمتة الصناعية والمعايرة",
             "تعرف على خدماتنا الهندسية في برمجة وحدات التحكم PLC وصيانة الصمامات الآلية وعقود الصيانة الوقائية.",
             "القدرات الهندسية الأساسية",
             "تقدم فرقنا في جبل علي والمصفح تصنيع لوحات التحكم والاختبار الميداني وعقود الصيانة السنوية.",
             ["تصنيع لوحات التحكم والقياس عن بُعد", "صيانة ومعايرة الصمامات الآلية MOV", "مراقبة الأنظمة الصناعية على مدار الساعة"],
             "صفر توقف غير مخطط له بفضل الصيانة الوقائية."),
            (sp_gulf, "post", "s7-1500-cold-chain-case-study", "Case Study: Cold-Chain SCADA Upgrade in Jebel Ali",
             "How redundant S7-1500 controllers and automated alarming reduced compressor energy consumption by 18%.",
             "Architecture & Measured Results",
             "By integrating Modbus TCP gateways with real-time differential pressure monitoring, operators gained sub-second visibility.",
             ["18% reduction in peak chiller kWh draw", "Automated SMS & email SLA escalation", "Full digital audit trail in ERPi Pro"],
             "Real-time telemetry turns reactive maintenance into predictable performance.",
             "دراسة حالة: تحديث أنظمة SCADA لسلاسل التبريد في جبل علي",
             "كيف ساهمت وحدات التحكم الاحتياطية في خفض استهلاك الطاقة بنسبة ١٨٪ وتحسين موثوقية التشغيل.",
             "البنية التقنية والنتائج المحققة",
             "من خلال ربط حساسات الضغط التفاضلي وأنظمة المراقبة اللحظية، تم تحقيق استجابة فورية للأعطال.",
             ["خفض استهلاك الطاقة بنسبة ١٨٪", "تنبيهات تلقائية فورية لفرق الصيانة", "سجل رقمي متكامل داخل نظام ERPi Pro"],
             "القياس اللحظي يحوّل الصيانة الطارئة إلى أداء مستقر."),
            (sp_gulf, "post", "hart-475-five-point-calibration-guide", "Best Practices for 5-Point HART Transmitter Calibration",
             "Field procedure for zero/span verification, loop current checks, and digital calibration certificates.",
             "5-Point Verification Checklist (0%, 25%, 50%, 75%, 100%)",
             "Consistent 5-point calibration eliminates sensor drift across critical boiler feedwater and desalination headers.",
             ["Verify 24V DC loop power & 250-ohm HART resistor", "Record As-Found and As-Left mA readings", "Attach digital certificate directly to the Asset record"],
             "Precision measurement is the foundation of safe process control.",
             "أفضل الممارسات لمعايرة أجهزة الإرسال الصناعية بنظام HART",
             "خطوات الفحص الخماسي وتوثيق شهادات المعايرة الرقمية للأصول الصناعية.",
             "قائمة التحقق الخماسية للمعايرة",
             "تضمن المعايرة المنتظمة دقة قراءات الضغط والتدفق في المحطات الحيوية.",
             ["فحص جهد الحلقة ٢٤ فولت ومقاومة الاتصال", "تسجيل القراءات قبل وبعد الضبط", "ربط شهادة المعايرة مباشرة بسجل الأصل"],
             "دقة القياس هي أساس التحكم الآمن في العمليات الصناعية."),
            # Abu Dhabi Hub
            (sp_auh, "page", "abu-dhabi-operations", "Abu Dhabi Energy & Calibration Hub",
             "Dedicated instrumentation laboratory and field commissioning support in Mussafah, Abu Dhabi.",
             "Serving Energy, Water & Petrochemical Operators",
             "Book lab calibration slots or review active service contracts directly from your Abu Dhabi customer portal.",
             ["Deadweight & Digital Pressure Calibration", "Ultrasonic & Electromagnetic Flow Verification", "Rapid On-Site Field Engineer Dispatch"],
             "Trusted by Abu Dhabi utility and industrial infrastructure teams.",
             "مركز أبوظبي للطاقة والمعايرة الصناعية",
             "مختبر معايرة متكامل ودعم هندسي ميداني في منطقة المصفح بأبوظبي.",
             "خدمة قطاعات الطاقة والمياه والبتروكيماويات",
             "احجز مواعيد المعايرة المختبرية أو تابع عقود الصيانة مباشرة عبر بوابة العملاء.",
             ["معايرة أجهزة الضغط الرقمية والقياسية", "فحص عدادات التدفق الكهرومغناطيسية", "استجابة ميدانية سريعة في أبوظبي والرويس"],
             "شريك موثوق لمشاريع البنية التحتية والطاقة في أبوظبي."),
            # Sharjah Logistics
            (sp_shj, "page", "sharjah-logistics-hub", "Sharjah Automation & Fleet Supply Portal",
             "Regional cable, switchgear, and spare parts distribution from SAIF Zone, Sharjah.",
             "Fast Dispatch Across Northern Emirates",
             "Track scheduled dock deliveries, bulk cable availability, and recurring supply agreements.",
             ["Same-Day Dispatch to Sharjah, Ajman & RAK", "Armored Instrumentation & Bus Cables in Stock", "Integrated Inter-Branch Stock Transfers"],
             "Keeping your supply chain moving without bottlenecks.",
             "بوابة الشارقة للإمداد اللوجستي وقطع الغيار",
             "مركز التوزيع الإقليمي للكابلات الصناعية ولوحات التوزيع وقطع الغيار في المنطقة الحرة بمطار الشارقة.",
             "توريد سريع لجميع الإمارات الشمالية",
             "تابع مواعيد الاستلام وتوافر الكابلات وقطع الغيار وعقود التوريد الدورية.",
             ["شحن في نفس اليوم إلى الشارقة وعجمان ورأس الخيمة", "توافر دائم للكابلات المصفحة وكابلات التحكم", "ربط مباشر مع مخازن دبي وأبوظبي"],
             "نضمن استمرارية سلاسل الإمداد دون توقف."),
        ]

        published_posts = []
        for idx, (sp, etype, slug_base, en_t, en_s, en_h, en_p, en_b, en_q, ar_t, ar_s, ar_h, ar_p, ar_b, ar_q) in enumerate(cms_catalog, start=1):
            tgroup = uuid.uuid5(uuid.NAMESPACE_DNS, f"erpipro.northwind.{sp.code}.{slug_base}")
            en_data = make_doc(en_t, en_s, en_h, en_p, en_b, en_q)
            ar_data = make_doc(ar_t, ar_s, ar_h, ar_p, ar_b, ar_q)

            en_entry, created_en = ContentEntry.objects.update_or_create(
                space=sp,
                entry_type=etype,
                locale="en",
                working_slug=slug_base,
                defaults={
                    "translation_group_id": tgroup,
                    "status": "published",
                    "published_slug": slug_base,
                    "working_data": en_data,
                    "published_data": en_data,
                    "has_unpublished_changes": False,
                    "published_at": now - timedelta(days=10 - idx),
                    "created_by": admin,
                    "updated_by": admin,
                },
            )
            if created_en:
                ContentRevision.objects.create(
                    space=sp,
                    entry=en_entry,
                    revision_number=1,
                    slug=slug_base,
                    snapshot_data=en_data,
                    published_by=admin,
                )

            ar_slug = f"{slug_base}-ar"
            ar_entry, created_ar = ContentEntry.objects.update_or_create(
                space=sp,
                entry_type=etype,
                locale="ar",
                working_slug=ar_slug,
                defaults={
                    "translation_group_id": tgroup,
                    "status": "published",
                    "published_slug": ar_slug,
                    "working_data": ar_data,
                    "published_data": ar_data,
                    "has_unpublished_changes": False,
                    "published_at": now - timedelta(days=10 - idx),
                    "created_by": admin,
                    "updated_by": admin,
                },
            )
            if created_ar:
                ContentRevision.objects.create(
                    space=sp,
                    entry=ar_entry,
                    revision_number=1,
                    slug=ar_slug,
                    snapshot_data=ar_data,
                    published_by=admin,
                )
            if etype == "post":
                published_posts.append((sp, en_entry, ar_entry))

            # Build Header & Footer navigation menus
            for loc, entry_obj, label_txt in (("en", en_entry, en_t[:32]), ("ar", ar_entry, ar_t[:32])):
                nav_hdr, _ = NavigationMenu.objects.get_or_create(space=sp, location="header", locale=loc)
                MenuItem.objects.get_or_create(
                    menu=nav_hdr,
                    sort_order=1,
                    defaults={"label": "Home" if loc == "en" else "الرئيسية", "target_type": "path", "target_path": "/", "is_active": True},
                )
                MenuItem.objects.get_or_create(
                    menu=nav_hdr,
                    sort_order=idx + 1,
                    defaults={"label": label_txt, "target_type": "entry", "target_entry": entry_obj, "is_active": True},
                )
                nav_ftr, _ = NavigationMenu.objects.get_or_create(space=sp, location="footer", locale=loc)
                MenuItem.objects.get_or_create(
                    menu=nav_ftr,
                    sort_order=1,
                    defaults={"label": "Customer Portal" if loc == "en" else "بوابة العملاء", "target_type": "path", "target_path": "/public/account", "is_active": True},
                )

            SitemapRefresh.objects.update_or_create(space=sp, defaults={"epoch": sp.cache_epoch, "due_at": now})

        # Add reader comments (approved & pending) on published posts
        if published_posts and portal_identities:
            sp_post, en_post, _ = published_posts[0]
            ident1, _ = portal_identities[0]
            ident2, _ = portal_identities[1]
            PublicSubmission.objects.get_or_create(
                space=sp_post,
                entry=en_post,
                external_identity_id=ident1.pk,
                body_text="Great architecture breakdown — our Jebel Ali facility saw immediate stability after migrating to S7-1500.",
                defaults={
                    "author_display_name": ident1.display_name,
                    "status": "approved",
                    "moderated_by": admin,
                    "moderated_at": now - timedelta(days=1),
                },
            )
            PublicSubmission.objects.get_or_create(
                space=sp_post,
                entry=en_post,
                external_identity_id=ident2.pk,
                body_text="Can we schedule a technical walkthrough for our Terminal 2 cold-chain expansion next month?",
                defaults={
                    "author_display_name": ident2.display_name,
                    "status": "pending",
                },
            )

        self.stdout.write(self.style.SUCCESS(
            "Northwind-scale demo data & 3 live Public Sites seeded successfully!\n"
            "  - Public Sites: /public/site/gulf-portal , /public/site/abu-dhabi-hub , /public/site/sharjah-logistics\n"
            "  - Customer Portal: /public/account (Login: customer1@erpipro.demo / Password: PortalDemo!2026)"
        ))