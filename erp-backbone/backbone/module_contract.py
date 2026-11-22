"""Read trusted module declarations. Permission codes remain the RBAC storage keys."""
from django.core.exceptions import ImproperlyConfigured


def register(manifest, metadata):
    if manifest.get("contract_version") not in ("0.1","1.0"):
        # Explicitly installed pre-contract extensions keep their legacy metadata.
        if "contract_version" not in manifest: return
        raise ImproperlyConfigured(f"Unsupported module contract: {manifest['id']}")
    key=manifest["id"]
    def require(condition, field):
        if not condition: raise ImproperlyConfigured(f"{key}: invalid {field}")
    require(manifest.get("category") in {"platform_core","shared_business","domain_module"},"category")
    require(manifest.get("scope_mode") in {"site_scoped","space_scoped","global"},"scope_mode")
    require(manifest.get("dependencies")==manifest.get("required_dependencies"),"dependencies")
    require(all(manifest.get("name",{}).get(lang) for lang in ("en","ar")) and manifest.get("version"),"identity")
    definitions=manifest.get("permission_definitions",{})
    require(set(definitions)==set(manifest.get("permissions",[])),"permissions")
    for code, labels in definitions.items():
        require(all(isinstance(labels.get(k),str) and labels[k].strip() for k in
                    ("name_en","name_ar","description_en","description_ar","action_type")),code)
        item={"label":{"en":labels["name_en"],"ar":labels["name_ar"]},"description":{
            "en":labels["description_en"],"ar":labels["description_ar"]},"type":labels["action_type"]}
        require(code not in metadata or metadata[code]==item,code+" conflicting definitions")
        metadata[code]=item
    for nav in manifest["navigation"]:
        require(nav["route"].startswith("/") and not nav["route"].startswith("//") and
                nav["required_permission"] in definitions and all(nav["label"].get(l) for l in ("en","ar")),"navigation")
    for field in ("events_published","events_subscribed"):
        require(isinstance(manifest[field],list) and all(isinstance(v,str) and v for v in manifest[field]),field)
    guides=manifest.get("sop_guides",[])
    require(isinstance(guides,list),"sop_guides")
    def text(value):
        return isinstance(value,str) and bool(value.strip())
    ids=set()
    for guide in guides:
        require(isinstance(guide,dict) and all(text(guide.get(k)) for k in ("id","title_en","title_ar","required_permission")),"guide metadata")
        require(guide["id"] not in ids and guide["required_permission"] in definitions,"guide id or permission")
        ids.add(guide["id"])
        for k in ("keywords_en","keywords_ar"):
            require(isinstance(guide.get(k),list) and all(text(v) for v in guide[k]),k)
        require(isinstance(guide.get("steps"),list) and guide["steps"],"guide steps")
        for step in guide["steps"]:
            require(isinstance(step,dict) and all(text(step.get(k)) for k in ("text_en","text_ar")),"guide step")
            require(isinstance(step.get("fields",[]),list) and all(text(v) for v in step.get("fields",[])),"guide fields")
            if "screenshot" in step:
                path=step["screenshot"]
                require(text(path) and path.startswith("docs/screenshots/") and ".." not in path.split("/") and "\\" not in path,"guide screenshot")
