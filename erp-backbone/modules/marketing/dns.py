import dns.exception
import dns.resolver
from django.core.cache import cache

from .providers import ses_client


def lookup(name, record):
    try:
        resolver = dns.resolver.Resolver()
        return list(resolver.resolve(name, record, lifetime=3))
    except (dns.exception.DNSException, OSError):
        return []


def txt(name):
    return [b"".join(record.strings).decode("ascii", "ignore").lower() for record in lookup(name, "TXT")]


def preflight(account):
    if account.adapter != "ses":
        return {"ok": True, "checks": {}}
    key=f"marketing:ses:dns:{account.pk}:{account.revision}"
    cached=cache.get(key)
    if cached is not None:
        return cached
    from botocore.exceptions import BotoCoreError, ClientError

    domain = account.sender.rsplit("@", 1)[-1].lower()
    region = account.config["region"]
    try:
        client = ses_client(region)
        identity = client.get_email_identity(EmailIdentity=domain)
        sending = client.get_account()
    except (BotoCoreError, ClientError, OSError):
        return {"ok": False, "checks": {"ses_api": False}}

    dkim = identity.get("DkimAttributes", {})
    mail_from_info = identity.get("MailFromAttributes", {})
    mail_from = mail_from_info.get("MailFromDomain", "").lower()
    records = txt("_dmarc." + domain)
    policies = [value for value in records if value.startswith("v=dmarc1")]
    policy = dict(part.strip().split("=", 1) for part in policies[0].split(";") if "=" in part) if len(policies) == 1 else {}
    spf_records = [value for value in txt(mail_from) if value.startswith("v=spf1")] if mail_from else []
    expected_mx = f"feedback-smtp.{region}.amazonses.com"
    selectors = dkim.get("Tokens", [])
    signing_zone = (dkim.get("SigningHostedZone") or "dkim.amazonses.com").rstrip(".").lower()
    checks = {
        "identity": identity.get("VerifiedForSendingStatus", False),
        "dkim": bool(dkim.get("SigningEnabled") and dkim.get("Status") == "SUCCESS" and selectors and all(
            any(str(answer.target).rstrip(".").lower() == f"{selector.lower()}.{signing_zone}"
                for answer in lookup(f"{selector}._domainkey.{domain}", "CNAME")) for selector in selectors
        )),
        "mail_from": mail_from.endswith("." + domain) and mail_from_info.get("MailFromDomainStatus") == "SUCCESS",
        "spf": len(spf_records) == 1 and "include:amazonses.com" in spf_records[0].split(),
        "dmarc": policy.get("p") in {"none", "quarantine", "reject"} and policy.get("aspf", "r") == "r",
        "mx": bool(mail_from and any(str(record.exchange).rstrip(".").lower() == expected_mx for record in lookup(mail_from, "MX"))),
        "production_access": sending.get("ProductionAccessEnabled", False),
    }
    result={"ok":all(checks.values()),"checks":checks}
    if result["ok"]:
        cache.set(key,result,60)
    return result
