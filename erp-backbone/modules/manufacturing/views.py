from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.views import day_field
from backbone.access import audit, has_permission, scopes, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.models import ModuleState, Site
from backbone.registry import enabled_modules
from backbone.services.pagination import paginate, positive_filter
from inventory.models import Item, Location, Unit
from inventory.stock import business_date, locked_site
from purchasing.views import create_draft as draft_purchase, fields as purchase_fields, issued_orders_for_planning
from supply.reservations import available, held
from supply.views import create_draft as draft_transfer

from . import bills, planning, production
from .bills import get, item, place, qty
from .models import Bill, Order, Plan, Policy, Proposal

MODULE = "manufacturing"


def scoped(user, perm, site_id):
    require(user, "manufacturing.view", site_id)
    require(user, MODULE+"."+perm, site_id)


def site_id(data):
    return identifier(data.get("site_id"), "site_id")


def filter_site(request, rows):
    site = positive_filter(request.GET, "site")
    return rows.filter(site_id=site) if site else rows


def revision(row, data):
    if row.revision != identifier(data.get("revision"), "revision"):
        raise Problem("revision_conflict", 409)
    row.revision += 1


def bill_data(row):
    return {"id":row.pk,"site_id":row.site_id,"item_id":row.item_id,"item":row.item.name,"unit":row.item.base_unit.code,
            "version":row.version,"effective":row.effective.isoformat(),"quantity":str(row.quantity),
            "components":row.components,"state":row.state,"revision":row.revision}


@endpoint("GET", module=MODULE)
def catalog(request):
    user = request.user
    require(user, "manufacturing.view")
    allowed = scopes(user,"manufacturing.view")
    sites = Site.objects.filter(active=True)
    if None not in allowed:
        sites = sites.filter(pk__in=allowed)
    locations = Location.objects.filter(warehouse__site__in=sites, warehouse__active=True).select_related("warehouse")
    purchase_orders = []
    if "purchase_orders" in enabled_modules():
        planning_sites = scopes(user,"manufacturing.plan")
        selected = sites if None in planning_sites else sites.filter(pk__in=planning_sites)
        purchase_orders = issued_orders_for_planning(selected)
    return {"sites":list(sites.order_by("name").values("id","name")),
            "purchase_orders":purchase_orders,
            "items":list(Item.objects.filter(active=True).order_by("code").values("id","name","code","base_unit_id")),
            "units":list(Unit.objects.order_by("code").values("id","code")),
            "locations":[{"id":row.pk,"site_id":row.warehouse.site_id,"name":f"{row.warehouse.name} / {row.name}"}
                         for row in locations.order_by("warehouse__site_id","warehouse__name","name")]}


@endpoint("GET","POST", module=MODULE)
def bom_list(request):
    user = request.user
    require(user, "manufacturing.view")
    if request.method=="GET":
        rows = filter_site(request, visible(Bill.objects.select_related("item__base_unit"), user,"manufacturing.view"))
        return paginate(request, rows.order_by("-pk"), bill_data)
    data = body(request)
    site = site_id(data)
    scoped(user,"bom",site)

    def create():
        locked_site(site)
        product = item(data)
        row = Bill.objects.create(site_id=site, item=product, version=bills.next_version(site,product.pk),
                                  effective=day_field(data,"effective"), quantity=qty(data), components=bills.components(data,product,site))
        audit(user,"manufacturing.bom.create",row.pk)
        return bill_data(row)
    return once(request,"manufacturing.bom.create",data,create)


@endpoint("GET","POST", module=MODULE)
def bom(request, pk):
    user = request.user
    rows = visible(Bill.objects.select_related("item__base_unit"),user,"manufacturing.view")
    row = get(rows,pk)
    if request.method=="GET":
        return bill_data(row)
    scoped(user,"bom",row.site_id)
    data = body(request)

    def commit():
        locked_site(row.site_id)
        current = get(rows.select_for_update(of=("self",)),pk)
        revision(current,data)
        mode = data.get("action")
        if mode=="release" and current.state=="draft":
            bills.validate_release(current)
            current.state = "released"
        elif mode=="retire" and current.state=="released":
            current.state = "retired"
            dates = Bill.objects.filter(site_id=current.site_id,state="released").values_list("effective",flat=True)
            for day in sorted(set(dates)):
                remaining = bills.effective_bills(current.site_id,day)
                if remaining.get(current.item_id) == current:
                    previous = Bill.objects.filter(site_id=current.site_id,item_id=current.item_id,state="released",
                                                   effective__lte=day).exclude(pk=current.pk).order_by("-effective").first()
                    if previous:
                        remaining[current.item_id] = previous
                    else:
                        remaining.pop(current.item_id,None)
                bills.assert_acyclic(remaining)
        elif mode=="edit" and current.state=="draft":
            current.effective = day_field(data,"effective")
            current.quantity = qty(data)
            current.components = bills.components(data,current.item,current.site_id)
        else:
            raise Problem("mfg_invalid_state",409)
        current.save()
        audit(user,"manufacturing.bom."+mode,current.pk)
        return bill_data(current)
    return once(request,"manufacturing.bom.update",{"id":pk,"data":data},commit)


def order_data(row, detail=False, user=None):
    info = {"id":row.pk,"number":row.number,"site_id":row.site_id,"item_id":row.item_id,"item":row.item.name,
            "source_id":row.source_id,"destination_id":row.destination_id,"due":row.due.isoformat(),"quantity":str(row.quantity),
            "bill_id":row.bill_id,"state":row.state,"revision":row.revision}
    if detail:
        info.update(materials=[{"id":line.pk,"item":line.item.name,"source_id":line.source_id,"required":str(line.required),
                     "consumed":str(used),"reserved":str(reserved),
                     "shortage":str(max(Decimal(0),line.required-used-reserved)), "snapshot":line.snapshot}
                    for line in row.materials.select_related("item")
                    for used,reserved in [(production.usable(line),held(production.owner(line)))]],
                    batches=[{"id":batch.pk,"quantity":str(batch.quantity),"held":batch.movement_id is None,
                              "at":batch.received_at.isoformat(),"movement_id":batch.movement_id}
                             for batch in row.batches.all()],
                    activities=[{"id":act.pk,"material":act.material.item.name,"action":act.action,"quantity":str(act.quantity),
                                 "value":str(act.value),"reason":act.reason,"movement_id":act.movement_id,
                                 "source_id":act.movement.location_id,
                                 "entry_id":act.movement.posting.entry_id}
                                 for act in row.activities.select_related("material__item","movement__posting")],
                    exceptions=[{"id":event.pk,"kind":event.kind,"material":event.material.item.name,"state":event.state,
                                 "reporter":event.reporter.get_username(),"quantity":str(event.quantity),"reason":event.reason,
                                 "decision_reason":event.decision_reason} for event in row.exceptions.select_related("reporter","material__item")],
                    cost=str(production.material_cost(row)),
                    actions=[perm for perm in ("prepare","execute","approve")
                             if has_permission(user,MODULE+"."+perm,row.site_id)])
    return info


def new_order(user, data, site):
    scoped(user,"prepare",site)
    locked_site(site)
    row = Order.objects.create(site_id=site,item=item(data),source=place(data,"source_id",site),
                               destination=place(data,"destination_id",site),due=day_field(data,"due"),quantity=qty(data))
    audit(user,"manufacturing.order.create",row.number)
    return row


@endpoint("GET","POST", module=MODULE)
def orders(request):
    user = request.user
    require(user,"manufacturing.view")
    if request.method=="GET":
        rows = filter_site(request,visible(Order.objects.select_related("item"),user,"manufacturing.view"))
        return paginate(request,rows.order_by("-pk"),order_data)
    data = body(request)
    site = site_id(data)
    scoped(user,"prepare",site)
    return once(request,"manufacturing.order.create",data,lambda:order_data(new_order(user,data,site),True,user))


ACTIONS = {"release":"prepare","reserve":"prepare","cancel":"prepare","issue":"execute","return":"execute",
           "receive":"execute","close":"prepare","report":"execute","decide":"approve",
           "edit":"prepare","change-source":"prepare","undo-receipt":"execute"}


@endpoint("GET","POST", module=MODULE)
def work(request, pk, operation=None):
    user = request.user
    rows = visible(Order.objects.select_related("item","source","destination"),user,"manufacturing.view")
    row = get(rows,pk)
    if request.method=="GET" and operation is None:
        return order_data(row,True,user)
    if request.method!="POST" or operation not in ACTIONS:
        raise Problem("method_not_allowed",405)
    scoped(user,ACTIONS[operation],row.site_id)
    data = body(request)

    def commit():
        if operation=="release" and not ModuleState.objects.select_for_update().get(module_id=MODULE).enabled:
            raise Problem("module_disabled",404)
        locked = get(rows.select_for_update(of=("self",)),pk)
        settings = locked_site(locked.site_id)
        revision(locked,data)
        reason = ""
        if operation=="release" and locked.state=="draft":
            production.release(locked,settings)
        elif operation=="cancel" and locked.state in ("draft","released"):
            reason = production.cancel(locked,data)
        elif operation=="edit" and locked.state=="draft":
            locked.item = item(data)
            locked.source = place(data,"source_id",locked.site_id)
            locked.destination = place(data,"destination_id",locked.site_id)
            locked.due = day_field(data,"due")
            locked.quantity = qty(data)
        elif locked.state!="released" or operation in ("release","edit"):
            raise Problem("mfg_invalid_state",409)
        elif operation=="reserve":
            production.reserve_materials(locked)
        elif operation=="change-source":
            reason = production.change_source(locked, production.material_for(locked,data), data)
        elif operation=="issue":
            production.issue(locked,user,settings,production.material_for(locked,data),qty(data))
        elif operation=="return":
            reason = string(data,"reason",500)
            production.against_issue(locked,user,settings,production.original_for(locked,data),qty(data),reason)
        elif operation=="receive":
            production.receive(locked,user,data)
        elif operation=="undo-receipt":
            reason = string(data,"reason",500)
            batch = get(locked.batches.filter(movement__isnull=True),identifier(data.get("batch_id"),"batch_id"))
            batch.delete()
        elif operation=="close":
            reason = production.close(locked,user,settings,data)
        elif operation=="report":
            reason = production.request_exception(locked,user,data)
        else:
            reason = production.decide(locked,user,settings,data)
        locked.save()
        audit(user,"manufacturing.order."+operation,f"{locked.number} · {reason}")
        return order_data(locked,True,user)
    return once(request,"manufacturing.order."+operation,{"id":pk,"data":data},commit)


def policy_data(row):
    return {"id":row.pk,"item_id":row.item_id,"item":row.item.name,"site_id":row.site_id,"method":row.method,
            "lead_days":row.lead_days,"source_id":row.source_id,"destination_id":row.destination_id}


@endpoint("GET","POST", module=MODULE)
def policies(request):
    user = request.user
    require(user,"manufacturing.view")
    if request.method=="GET":
        rows = filter_site(request,visible(Policy.objects.select_related("item"),user,"manufacturing.view"))
        return paginate(request,rows.order_by("item__code","pk"),policy_data)
    data = body(request)
    site = site_id(data)
    scoped(user,"plan",site)
    method, lead = data.get("method"), data.get("lead_days")
    if method not in ("make","buy","transfer"):
        raise Problem("invalid_method")
    if type(lead) is not int or not 0<=lead<=365:
        raise Problem("invalid_lead_days",field="lead_days")
    with transaction.atomic():
        locked_site(site)
        product = item(data)
        source = place(data,"source_id",site if method=="make" else None) if method!="buy" else None
        destination = place(data,"destination_id",site) if method!="buy" else None
        if method=="transfer":
            require(user,"manufacturing.view",source.warehouse.site_id)
            if source.warehouse.site_id==site:
                raise Problem("mfg_transfer_source",409)
        row,_ = Policy.objects.update_or_create(site_id=site,item=product,defaults={
            "method":method,"lead_days":lead,"source":source,"destination":destination})
        audit(user,"manufacturing.policy.save",row.pk)
        return policy_data(row)


def proposal_data(row):
    links = (("manufacturing/orders",row.production_id),("purchase-requests",row.purchase_id),("supply/transfers",row.transfer_id))
    target = next((f"/{path}/{pk}" for path,pk in links if pk),None)
    return {"id":row.pk,"item_id":row.item_id,"item":row.item.name,"quantity":str(row.quantity),"method":row.method,
            "due":row.due.isoformat(),"start":row.start.isoformat(),"facts":row.facts,"target":target}


def plan_data(row, detail=False):
    result = {"id":row.pk,"site_id":row.site_id,"start":row.start.isoformat(),"end":row.end.isoformat(),"inputs":row.inputs}
    if detail:
        result["proposals"] = [proposal_data(proposal) for proposal in row.proposals.select_related("item").order_by("due","pk")]
    return result


def plan_inputs(data):
    required, eta = data.get("requirements",[]), data.get("purchase_dates",{})
    if not isinstance(required,list) or len(required)>100 or not all(isinstance(row,dict) for row in required):
        raise Problem("invalid_requirements")
    if not isinstance(eta,dict) or len(eta)>100 or any(not key.isascii() or not key.isdecimal() or len(key)>18 for key in eta):
        raise Problem("invalid_purchase_dates")
    requirements = [{"item_id":item(row).pk,"quantity":str(qty(row)),"date":day_field(row,"date")} for row in required]
    dates = {key:day_field({"date":value},"date") for key,value in eta.items()}
    inputs = data.get("purchase_mappings",[])
    if not isinstance(inputs,list) or len(inputs)>100 or not all(isinstance(row,dict) for row in inputs):
        raise Problem("invalid_purchase_mappings")
    mappings = {}
    for line in inputs:
        key = (identifier(line.get("order_id"),"order_id"),identifier(line.get("line_id"),"line_id"))
        if key in mappings:
            raise Problem("invalid_purchase_mappings")
        mappings[key] = (item(line),identifier(line.get("unit_id"),"unit_id",optional=True))
    return requirements,dates,mappings


@endpoint("GET","POST", module=MODULE)
def plans(request):
    user = request.user
    require(user,"manufacturing.view")
    if request.method=="GET":
        rows = filter_site(request,visible(Plan.objects.all(),user,"manufacturing.view"))
        return paginate(request,rows.order_by("-pk"),plan_data)
    data = body(request)
    site = site_id(data)
    scoped(user,"plan",site)

    def create():
        settings = locked_site(site)
        start,end = planning.horizon(data,business_date(settings,timezone.now()))
        requirements,dates,mappings = plan_inputs(data)
        row = Plan.objects.create(site_id=site,start=start,end=end,created_by=user,inputs=data)
        excluded = planning.calculate(row,requirements,dates,mappings)
        row.inputs = {**data,"excluded":excluded}
        row.save(update_fields=["inputs"])
        audit(user,"manufacturing.mrp.run",row.pk)
        return plan_data(row,True)
    return once(request,"manufacturing.mrp.run",data,create)


@endpoint("GET", module=MODULE)
def plan(request, pk):
    return plan_data(get(visible(Plan.objects.all(),request.user,"manufacturing.view"),pk),True)


@endpoint("POST", module=MODULE)
def convert(request, pk):
    user, data = request.user, body(request)
    rows = Proposal.objects.select_related("plan","item").filter(plan__in=visible(Plan.objects.all(),user,"manufacturing.view"))
    current = get(rows,pk)
    site = current.plan.site_id
    scoped(user,"plan",site)

    def commit():
        row = get(rows.select_for_update(of=("self",)),pk)
        if row.production_id or row.purchase_id or row.transfer_id:
            return proposal_data(row)
        operation = row.method
        source_id = data.get("source_id") or row.facts["source_id"]
        destination_id = data.get("destination_id") or row.facts["destination_id"]
        source = place({"source_id":source_id},"source_id") if operation=="transfer" else None
        for location_site in sorted({site,source.warehouse.site_id} if source else {site}):
            settings = locked_site(location_site)
            if location_site==site:
                today = business_date(settings,timezone.now())
        if row.due < today:
            raise Problem("mfg_plan_stale",409)
        requirements,dates,mappings = plan_inputs(row.plan.inputs)
        fresh = planning.calculate(row.plan,requirements,dates,mappings,persist=False)
        if not any((candidate.item_id,candidate.due,candidate.method,candidate.facts)==(row.item_id,row.due,row.method,row.facts)
                   and candidate.quantity==row.quantity for candidate in fresh):
            raise Problem("mfg_plan_stale",409)
        qty_string = format(row.quantity,"f")
        if operation=="make":
            if row.facts["bill_id"] is None:
                raise Problem("mfg_bom_missing",409)
            row.production = new_order(user,{"item_id":row.item_id,"source_id":source_id,"destination_id":destination_id,
                                            "quantity":qty_string,"due":row.due.isoformat()},site)
        elif operation=="buy":
            if "purchasing" not in enabled_modules():
                raise Problem("module_disabled",404)
            quantity = row.quantity.quantize(Decimal("0.001"))
            if row.quantity != quantity:
                raise Problem("mfg_purchase_precision",409)
            values = purchase_fields({"site_id":site,"title":f"MRP #{row.plan_id} · {row.item.code}","currency":"AED",
                         "lines":[{"description":row.item.name,"quantity":format(quantity,".3f"),
                                   "unit_price":data.get("unit_price","0")}]})
            row.purchase = draft_purchase(user, values)
        else:
            if source.warehouse.site_id==site:
                raise Problem("mfg_transfer_source",409)
            if available(source.warehouse.site_id,row.item_id,source.pk)<row.quantity:
                raise Problem("supply_insufficient_available",409)
            row.transfer = draft_transfer(user,{"site_id":source.warehouse.site_id,"destination_id":site,
                        "note":f"MRP #{row.plan_id}","lines":[{"item_id":row.item_id,"source_id":source.pk,
                        "destination_id":destination_id,"quantity":qty_string}]})
        row.save(update_fields=["production","purchase","transfer"])
        audit(user,"manufacturing.mrp.convert",f"#{row.pk} · {operation}")
        return proposal_data(row)
    return once(request,"manufacturing.mrp.convert",{"id":pk,"data":data},commit)
