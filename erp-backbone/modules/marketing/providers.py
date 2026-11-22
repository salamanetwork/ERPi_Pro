import base64
import hashlib
import hmac
import json
import logging
import re
import smtplib
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)


class ProviderFailure(Exception):
    def __init__(self, code, *, retry=False, unknown=False, delay=0):
        self.code, self.retry, self.unknown, self.delay = code, retry, unknown, delay


class NotificationProvider:
    def send(self, account, message):
        raise NotImplementedError

    def verify_webhook(self, account, request, payload=None):
        return False


class MailpitProvider(NotificationProvider):
    def send(self, account, message):
        try:
            smtp = smtplib.SMTP(settings.ERP_MARKETING_SMTP_HOST, settings.ERP_MARKETING_SMTP_PORT, timeout=8)
        except OSError:
            raise ProviderFailure("smtp_unavailable", retry=True) from None
        try:
            with smtp:
                refused = smtp.send_message(message)
                if refused:
                    raise ProviderFailure("smtp_rejected")
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [code for code, _ in exc.recipients.values()]
            raise ProviderFailure("smtp_rejected", retry=any(400 <= code < 500 for code in codes)) from None
        except smtplib.SMTPResponseException as exc:
            raise ProviderFailure("smtp_rejected", retry=400 <= exc.smtp_code < 500) from None
        except (smtplib.SMTPServerDisconnected, OSError):
            raise ProviderFailure("smtp_unconfirmed", unknown=True) from None
        return message["Message-ID"], "delivered"


class MockProvider(NotificationProvider):
    def send(self, account, message):
        return message["Message-ID"], "delivered"


@lru_cache(maxsize=8)
def ses_client(region):
    import boto3
    from botocore.config import Config

    return boto3.client("sesv2", region_name=region, config=Config(
        connect_timeout=5, read_timeout=8, retries={"total_max_attempts": 1},
    ))


class SesProvider(NotificationProvider):
    def verify_webhook(self, account, request, payload=None):
        return verified_sns(account, payload)

    def send(self, account, message):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            result = ses_client(account.config["region"]).send_email(
                FromEmailAddress=str(message["From"]),
                Destination={"ToAddresses": [str(message["To"])]},
                Content={"Raw": {"Data": message.as_bytes()}},
                ConfigurationSetName=account.config["configuration_set"],
            )
        except ClientError as exc:
            code = exc.response["Error"].get("Code", "")
            metadata = exc.response.get("ResponseMetadata", {})
            status = metadata.get("HTTPStatusCode", 0)
            if status == 429 or code in ("TooManyRequestsException", "Throttling", "ThrottlingException"):
                delay = retry_after(metadata.get("HTTPHeaders", {}).get("retry-after", ""))
                raise ProviderFailure("ses_throttled", retry=True, delay=delay) from None
            raise ProviderFailure("ses_rejected", unknown=status >= 500) from None
        except (BotoCoreError, OSError):
            raise ProviderFailure("ses_unconfirmed", unknown=True) from None
        return result["MessageId"], "accepted"


def retry_after(value):
    if value and value.isascii() and value.isdigit():
        return min(int(value), 2**31 - 1)
    if value:
        try:
            return max(0, int((parsedate_to_datetime(value) - timezone.now()).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            pass
    return 0


class TwilioProvider(NotificationProvider):
    def verify_webhook(self, account, request, payload=None):
        return verified_twilio(account, request)

    def send(self, account, message):
        sid, token = settings.ERP_TWILIO_ACCOUNT_SID, settings.ERP_TWILIO_AUTH_TOKEN
        if not re.fullmatch(r"AC[0-9a-fA-F]{32}", sid) or not token or not settings.ERP_MARKETING_PUBLIC_URL.startswith("https://"):
            raise ProviderFailure("twilio_configuration")
        prefix = "whatsapp:" if account.channel == "whatsapp" else ""
        fields = {
            "From": prefix + account.sender,
            "To": prefix + str(message["To"]),
            "StatusCallback": settings.ERP_MARKETING_PUBLIC_URL + f"/api/marketing/webhooks/twilio/{account.pk}",
        }
        if prefix:
            fields.update(ContentSid=str(message["X-Provider-Template"]), ContentVariables=str(message["X-Provider-Variables"] or "{}"))
        else:
            fields["Body"] = str(message.get_content()).strip()
        auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
        request = Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                          data=urlencode(fields).encode(), headers={"Authorization": "Basic " + auth})
        try:
            with urlopen(request, timeout=8) as response:
                result = json.load(response)
        except HTTPError as exc:
            if exc.code == 429:
                raise ProviderFailure("twilio_throttled", retry=True, delay=retry_after(exc.headers.get("Retry-After", ""))) from None
            raise ProviderFailure("twilio_rejected", unknown=exc.code >= 500) from None
        except (URLError, OSError, ValueError):
            raise ProviderFailure("twilio_unconfirmed", unknown=True) from None
        return result["sid"], "accepted"


PROVIDERS = {"mailpit": MailpitProvider(), "mock": MockProvider(), "ses": SesProvider(), "twilio": TwilioProvider()}


def enabled(account):
    return account.enabled and (settings.ERP_MARKETING_LOCAL if account.adapter in {"mailpit", "mock"} else settings.ERP_MARKETING_LIVE)


def mail(delivery, attempt_number, token_value=None):
    message = EmailMessage()
    message["From"] = delivery.provider.sender
    message["To"] = delivery.address
    message["Subject"] = delivery.subject
    message["Message-ID"] = f"<marketing-{delivery.pk}-{attempt_number}@{delivery.provider.sender.rsplit('@', 1)[-1]}>" if delivery.provider.channel == "email" else f"<marketing-{delivery.pk}-{attempt_number}@erp.invalid>"
    content = delivery.body
    if token_value and delivery.kind == "confirm":
        content += f"\n\n{settings.ERP_MARKETING_PUBLIC_URL}/api/marketing/confirm/{token_value}"
    elif token_value:
        url = f"{settings.ERP_MARKETING_PUBLIC_URL}/api/marketing/unsubscribe/{token_value}"
        if delivery.provider.adapter == "ses" and not url.startswith("https://"):
            raise ProviderFailure("unsubscribe_requires_https")
        content += f"\n\nUnsubscribe / إلغاء الاشتراك: {url}"
        message["List-Unsubscribe"] = f"<{url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if delivery.provider_template:
        message["X-Provider-Template"] = delivery.provider_template
        message["X-Provider-Variables"] = json.dumps(delivery.variables, ensure_ascii=True, separators=(",", ":"))
    message.set_content(content)
    return message


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@lru_cache(maxsize=16)
def sns_certificate(url):
    from cryptography.x509 import load_pem_x509_certificate

    parsed = urlparse(url)
    if (
        parsed.scheme != "https" or not re.fullmatch(r"sns\.[a-z0-9-]+\.amazonaws\.com", parsed.netloc)
        or not re.fullmatch(r"/SimpleNotificationService-[a-zA-Z0-9]+\.pem", parsed.path)
        or parsed.query or parsed.fragment
    ):
        raise ValueError("invalid certificate URL")
    with build_opener(NoRedirect()).open(url, timeout=5) as response:
        return load_pem_x509_certificate(response.read(32768)).public_key()


def verified_sns(account, payload):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    if payload.get("Type") not in {"Notification","SubscriptionConfirmation"} or not account.config.get("topic_arn") or payload.get("TopicArn") != account.config["topic_arn"]:
        return False
    if payload.get("SignatureVersion") != "2":
        return False
    if payload["Type"]=="SubscriptionConfirmation":
        fields=["Message","MessageId","SubscribeURL","Timestamp","Token","TopicArn","Type"]
    else:
        fields=["Message","MessageId"]+(["Subject"] if "Subject" in payload else [])+["Timestamp","TopicArn","Type"]
    try:
        canonical = "".join(f"{key}\n{payload[key]}\n" for key in fields).encode()
        sns_certificate(payload["SigningCertURL"]).verify(
            base64.b64decode(payload["Signature"], validate=True), canonical, padding.PKCS1v15(), hashes.SHA256()
        )
        return True
    except (InvalidSignature, ValueError, TypeError, KeyError, OSError):
        return False


def verified_twilio(account, request):
    token = settings.ERP_TWILIO_AUTH_TOKEN
    if not token or request.POST.get("AccountSid") != settings.ERP_TWILIO_ACCOUNT_SID:
        return False
    url = settings.ERP_MARKETING_PUBLIC_URL + f"/api/marketing/webhooks/twilio/{account.pk}"
    canonical = url + "".join(f"{key}{value}" for key in sorted(request.POST) for value in sorted(set(request.POST.getlist(key))))
    signed = base64.b64encode(hmac.new(token.encode(), canonical.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(signed, request.headers.get("X-Twilio-Signature", ""))
