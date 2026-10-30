"""Read-only S3 policy checks for the separate, versioned file backup."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import re
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET


class PolicyError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# A provider redirect must not forward signed headers/session tokens to another host.
urlopen = build_opener(_NoRedirect()).open


def _xml(data: bytes) -> ET.Element:
    root = ET.fromstring(data)
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    return root


def _get_bucket_setting(options: dict, bucket: str, setting: str) -> ET.Element:
    """Sign the two read-only S3 requests without an SDK or credential output."""
    endpoint = urlsplit(options.get("endpoint", ""))
    if endpoint.scheme != "https" or not endpoint.netloc or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment or endpoint.username:
        raise PolicyError("file backups require an explicit HTTPS S3 endpoint")
    access = options.get("access_key_id", "")
    secret = options.get("secret_access_key", "")
    if not access or not secret or str(options.get("env_auth", "false")).lower() == "true":
        raise PolicyError("file backups require explicit S3 credentials in the private rclone config")
    region = options.get("region") or "us-east-1"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    date = stamp[:8]
    path = "/" + quote(bucket, safe="")
    query = setting + "="
    body_hash = hashlib.sha256(b"").hexdigest()
    headers = {"host": endpoint.netloc, "x-amz-content-sha256": body_hash, "x-amz-date": stamp}
    if options.get("session_token"):
        headers["x-amz-security-token"] = options["session_token"]
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{key}:{headers[key]}\n" for key in sorted(headers))
    canonical = "\n".join(("GET", path, query, canonical_headers, signed, body_hash))
    scope = f"{date}/{region}/s3/aws4_request"
    to_sign = "\n".join(("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    key = ("AWS4" + secret).encode()
    for part in (date, region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed}, Signature={signature}"
    request = Request(f"https://{endpoint.netloc}{path}?{query}", headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return _xml(response.read())
    except HTTPError as exc:
        # A missing lifecycle policy is safe; a permission/network failure is not.
        if exc.code == 404 and setting == "lifecycle" and _xml(exc.read()).findtext("Code") == "NoSuchLifecycleConfiguration":
            return ET.Element("LifecycleConfiguration")
        raise PolicyError("cannot read S3 backup retention policy") from None


def validate_policy(versioning: ET.Element, lifecycle: ET.Element, prefix: str) -> None:
    if versioning.tag != "VersioningConfiguration" or versioning.findtext("Status") != "Enabled":
        raise PolicyError("S3 versioning must be Enabled")
    if lifecycle.tag != "LifecycleConfiguration":
        raise PolicyError("invalid S3 lifecycle response")
    for rule in lifecycle.findall("Rule"):
        if rule.findtext("Status") == "Disabled":
            continue
        # Unknown/tag/size filters are conservatively treated as possibly matching.
        rule_prefix = rule.findtext(".//Prefix") or ""
        overlaps = prefix.startswith(rule_prefix) or rule_prefix.startswith(prefix)
        if overlaps and any(node.tag in {"Expiration", "NoncurrentVersionExpiration", "NoncurrentDeleteMarkers"} for node in rule.iter()):
            raise PolicyError("file backup prefix must not expire current or previous versions")


def check_remote(configs: dict, remote: str) -> tuple[str, str, str]:
    """Only named crypt -> static S3 remotes are supported; ambiguity fails closed."""
    if not re.fullmatch(r"[\w-]+:", remote):
        raise PolicyError("file backup remote must be a named crypt root ending in a colon")
    crypt = configs.get(remote[:-1], {})
    if crypt.get("type") != "crypt":
        raise PolicyError("file backup remote must use crypt")
    backing = crypt.get("remote", "")
    raw_name, separator, location = backing.partition(":")
    raw = configs.get(raw_name, {})
    bucket, slash, prefix = location.partition("/")
    if not separator or raw.get("type") != "s3" or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", bucket) or not slash or not prefix or any(part in {"", ".", ".."} for part in location.removesuffix("/").split("/")) or "\\" in location:
        raise PolicyError("file backup crypt remote must have a dedicated S3 bucket prefix")
    prefix = prefix.rstrip("/") + "/"
    validate_policy(_get_bucket_setting(raw, bucket, "versioning"), _get_bucket_setting(raw, bucket, "lifecycle"), prefix)
    return raw.get("endpoint", ""), bucket, prefix
