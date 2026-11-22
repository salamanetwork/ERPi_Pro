from html.parser import HTMLParser
from urllib.parse import urlsplit
from uuid import UUID
from django.core.exceptions import ValidationError
from django.core.validators import validate_unicode_slug, URLValidator
from backbone.registry import enabled_modules
from backbone.validation import Problem


def invalid(field):
    raise Problem("validation_failed",field=field)


def uid(value,field):
    try: return UUID(str(value))
    except (ValueError,TypeError): invalid(field)


def choice(value,options,field):
    if not isinstance(value,str) or value not in options: invalid(field)
    return value


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts=[]
        self.skip=False

    def handle_starttag(self,tag,attrs):
        if tag in ("script","style"): self.skip=True

    def handle_endtag(self,tag):
        if tag in ("script","style"): self.skip=False

    def handle_data(self,data):
        if not self.skip: self.parts.append(data)


def text(value,field,limit,minimum=0):
    if not isinstance(value,str) or len(value)>limit*5 or "\0" in value: invalid(field)
    parser=Text()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError,AssertionError): invalid(field)
    result="".join(parser.parts).strip()
    if not minimum<=len(result)<=limit: invalid(field)
    return result


def slug(value):
    value=text(value,"working_slug",160,1)
    try: validate_unicode_slug(value)
    except ValidationError: invalid("working_slug")
    return value


def link(value,field):
    if not isinstance(value,str) or len(value)>500 or not value or "\\" in value or any(ord(c)<33 for c in value): invalid(field)
    if field=="target_path":
        if not value.startswith("/") or value.startswith("//"): invalid(field)
    else:
        try:
            URLValidator(schemes=["https"])(value)
            if urlsplit(value).username or urlsplit(value).password: invalid(field)
        except (ValueError,ValidationError): invalid(field)
    return value


def image(space,value,field):
    if value in ("",None): return None
    if "media_service" not in enabled_modules(): invalid(field)
    from media_service.services import public_image
    asset=uid(value,field)
    if not public_image(space.pk,asset): invalid(field)
    return str(asset)


def content(space,data):
    if not isinstance(data,dict): invalid("working_data")
    blocks=data.get("body_blocks",[])
    if not isinstance(blocks,list) or len(blocks)>200: invalid("body_blocks")
    output=[]
    for i,b in enumerate(blocks):
        field=f"body_blocks.{i}"
        if not isinstance(b,dict): invalid(field)
        kind=choice(b.get("type"),("heading","paragraph","list","quote","image","callout","code"),field)
        block={"type":kind}
        if kind=="image":
            block["media_id"]=image(space,b.get("media_id"),field)
            if not block["media_id"]: invalid(field)
            block["alt"]=text(b.get("alt",""),field,300)
        elif kind=="list":
            items=b.get("items")
            if not isinstance(items,list) or not 1<=len(items)<=100: invalid(field)
            block["items"]=[text(x,field,1500,1) for x in items]
        else: block["text"]=text(b.get("text"),field,16000,1)
        output.append(block)
    seo=data.get("seo_meta",{})
    if not isinstance(seo,dict): invalid("seo_meta")
    return {"title":text(data.get("title"),"title",200,1),"summary":text(data.get("summary",""),"summary",1500),
            "body_blocks":output,"cover_media_id":image(space,data.get("cover_media_id"),"cover_media_id"),
            "seo_meta":{"title":text(seo.get("title",""),"seo_meta.title",200),"description":text(seo.get("description",""),"seo_meta.description",500),
                        "robots":choice(seo.get("robots","index,follow"),("index,follow","noindex,follow","noindex,nofollow"),"seo_meta.robots"),
                        "og_image_media":image(space,seo.get("og_image_media"),"seo_meta.og_image_media")}}
