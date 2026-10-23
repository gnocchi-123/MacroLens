"""Opt-in KIS quotation transport. No account/order endpoints or import-time auth."""

import fcntl
import hashlib
import json
import math
import os
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .price_quality import AuditError

SERVERS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "demo": "https://openapivts.koreainvestment.com:29443",
}
ENDPOINTS = {
    "dailyprice": ("HHDFS76240000", "AUTH EXCD SYMB GUBN BYMD MODP"),
    "search-info": ("CTPF1702R", "PRDT_TYPE_CD PDNO"),
    "period-rights": (
        "CTRGT011R",
        "RGHT_TYPE_CD INQR_DVSN_CD INQR_STRT_DT INQR_END_DT PDNO PRDT_TYPE_CD "
        "CTX_AREA_NK50 CTX_AREA_FK50",
    ),
    "rights-by-ice": ("HHDFS78330900", "NCOD SYMB ST_YMD ED_YMD"),
}
SECRET_NAMES = {
    "appkey",
    "appsecret",
    "access_token",
    "authorization",
    "refresh_token",
    "kis_app_key",
    "kis_app_secret",
    "token",
    "headers",
    "cookie",
}


class KISError(AuditError):
    """Only fixed, non-sensitive diagnostic codes may cross the transport boundary."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise KISError("redirect_refused")


def transport(method, url, headers, body, timeout):
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            blob = response.read(8_000_001)
            if len(blob) > 8_000_000:
                raise KISError("response_too_large")
            return response.status, dict(response.headers), blob
    except HTTPError as exc:
        # Never retain error bodies, headers or exception text: they can echo credentials.
        return exc.code, {}, b"{}"
    except (URLError, TimeoutError, OSError):
        raise KISError("network_or_timeout") from None


def private_dir(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise KISError("cache_symlink_refused")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


@contextmanager
def private_file(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "r+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise KISError("another_kis_process_running") from None
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def read_state(stream):
    stream.seek(0)
    try:
        value = json.load(stream)
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


def write_state(stream, value):
    stream.seek(0)
    stream.truncate()
    json.dump(value, stream)
    stream.flush()
    os.fsync(stream.fileno())


def public_payload(blob, secrets=()):
    """Reject suspect market bodies entirely; do not claim redacted data are raw."""
    try:

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise KISError("duplicate_json_key")
                result[key] = value
            return result

        value = json.loads(blob, object_pairs_hook=unique_object)
        serialized = json.dumps(value, ensure_ascii=False)
        for secret in secrets:
            if secret and (secret in serialized or quote(secret, safe="") in serialized):
                raise KISError("credential_echo_refused")

        def walk(node):
            if isinstance(node, dict):
                for key, child in node.items():
                    if key.lower().replace("-", "_") in SECRET_NAMES:
                        raise KISError("credential_field_refused")
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        if not isinstance(value, dict):
            raise KISError("invalid_json_shape")
        return value
    except KISError:
        raise
    except (ValueError, UnicodeError):
        raise KISError("invalid_json") from None


class KISClient:
    def __init__(
        self,
        environment,
        cache_root=Path("data/.kis_auth"),
        *,
        send=transport,
        sleep=time.sleep,
        clock=time.time,
    ):
        if environment not in SERVERS:
            raise KISError("explicit_environment_required")
        self.environment = environment
        self.key = os.environ.get("KIS_APP_KEY", "")
        self.secret = os.environ.get("KIS_APP_SECRET", "")
        if not self.key or not self.secret:
            raise KISError("missing_KIS_APP_KEY_or_KIS_APP_SECRET")
        self.base = SERVERS[environment]
        self.cache = Path(cache_root) / environment
        self.send, self.sleep, self.clock = send, sleep, clock
        self.token_value = ""
        self.identity = hashlib.sha256((self.key + "\0" + self.secret).encode()).hexdigest()
        self.last_request = 0.0

    def token(self):
        private_dir(self.cache)
        with private_file(self.cache / "token.json") as stream:
            state = read_state(stream)
            now = self.clock()
            if (
                state.get("identity") == self.identity
                and state.get("token")
                and state.get("expires_at", 0) > now + 60
            ):
                self.token_value = state["token"]
                return self.token_value
            # Includes failed/uncertain POSTs: never repeat token issuance within a minute.
            if now - state.get("last_attempt", 0) < 61:
                raise KISError("token_cooldown_wait_61_seconds")
            state = {"identity": self.identity, "last_attempt": now}
            write_state(stream, state)
            body = json.dumps(
                {"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret}
            ).encode()
            status, _, blob = self.send(
                "POST", self.base + "/oauth2/tokenP", {"Content-Type": "application/json"}, body, 15
            )
            if status != 200:
                raise KISError("authentication_failed")
            try:
                response = json.loads(blob)
                token = response["access_token"]
                expiry = float(response["expires_in"])
                if (
                    not isinstance(token, str)
                    or not token
                    or not math.isfinite(expiry)
                    or expiry <= 60
                ):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                raise KISError("invalid_token_response") from None
            state.update(token=token, expires_at=now + min(expiry, 86400))
            write_state(stream, state)
            self.token_value = token
            return token

    def get(self, endpoint, params, continuation=""):
        if endpoint not in ENDPOINTS or set(params) != set(ENDPOINTS[endpoint][1].split()):
            raise KISError("quotation_contract_required")
        if continuation not in ("", "N") or any(not isinstance(v, str) for v in params.values()):
            raise KISError("invalid_request")
        public_payload(json.dumps(params).encode(), (self.key, self.secret, self.token_value))
        # AUTH is a public empty parameter in the official example, never an access token.
        if params.get("AUTH", ""):
            raise KISError("AUTH_must_be_empty")
        token = self.token()
        headers = {
            "Content-Type": "application/json",
            "authorization": "Bearer " + token,
            "appkey": self.key,
            "appsecret": self.secret,
            "tr_id": ENDPOINTS[endpoint][0],
            "tr_cont": continuation,
            "custtype": "P",
        }
        url = self.base + "/uapi/overseas-price/v1/quotations/" + endpoint
        for attempt in range(3):
            # Conservative one request/second for both servers, not a claimed KIS entitlement.
            self.sleep(max(0, 1.0 - (self.clock() - self.last_request)))
            self.last_request = self.clock()
            try:
                status, response_headers, blob = self.send(
                    "GET", url + "?" + urlencode(params), headers, None, 15
                )
            except KISError as exc:
                if str(exc) == "network_or_timeout" and attempt < 2:
                    self.sleep(2 ** (attempt + 1))
                    continue
                raise
            if status in (401, 403):
                if status == 401:
                    with private_file(self.cache / "token.json") as stream:
                        state = read_state(stream)
                        state.pop("token", None)
                        state["expires_at"] = 0
                        write_state(stream, state)
                raise KISError("authentication_or_permission_failed")
            if status == 429 or 500 <= status < 600:
                if attempt < 2:
                    self.sleep(2 ** (attempt + 1))
                    continue
                raise KISError("http_retry_exhausted")
            if status != 200:
                raise KISError("http_request_failed")
            data = public_payload(blob, (self.key, self.secret, token))
            if data.get("msg_cd") == "EGW00201" and attempt < 2:
                self.sleep(2 ** (attempt + 1))
                continue
            if data.get("rt_cd") != "0":
                raise KISError("provider_rejected_request")
            cont = {k.lower(): v for k, v in response_headers.items()}.get("tr_cont", "").strip()
            if cont not in ("", "M", "F", "D", "E"):
                raise KISError("unknown_continuation")
            return data, blob, cont
        raise KISError("rate_limit_exhausted")
