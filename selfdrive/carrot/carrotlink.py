import base64
import json
import os
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import jwt
import requests
from Crypto.PublicKey import ECC
from websocket import WebSocketBadStatusException, WebSocketException, WebSocketTimeoutException, create_connection

from openpilot.common.api import get_key_pair
from openpilot.common.params import Params
from openpilot.common.utils import atomic_write
from openpilot.system.hardware import HARDWARE, PC
from openpilot.system.hardware.hw import Paths
from openpilot.system.version import get_build_metadata


PAIRING_TTL = 5 * 60
DEFAULT_API_HOST = "https://commalink.gomeng-dev.com"
CONNECTION_RETRY_MAX = 60.0
CONNECTION_IDLE_TIMEOUTS = 12


class CarrotLinkError(Exception):
  pass


def _carrotlink_root(persist_root: str | Path | None = None) -> Path:
  # /persist is read-only on devices; /data is persistent and app-writable.
  return Path(persist_root or (Paths.persist_root() if PC else "/data")) / "carrotlink"


class CarrotLinkClient:
  def __init__(
    self, api_host: str | None = None, persist_root: str | Path | None = None, params: Params | None = None, session: requests.Session | None = None
  ):
    self.api_host = (api_host or os.getenv("CARROTLINK_API_HOST", DEFAULT_API_HOST)).rstrip("/")
    parsed = urlsplit(self.api_host)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
      raise CarrotLinkError("CARROTLINK_API_HOST must be an HTTPS origin")
    try:
      self.origin = (parsed.hostname, parsed.port or 443)
    except ValueError as e:
      raise CarrotLinkError("CARROTLINK_API_HOST must be an HTTPS origin") from e

    self.root = _carrotlink_root(persist_root)
    self.params = params or Params()
    self.session = session or requests.Session()

  def _require_offroad(self):
    if not self.params.get_bool("IsOffroad"):
      raise CarrotLinkError("CarrotLink pairing is only available offroad")

  def _write(self, path: Path, value: str, mode: int = 0o600, overwrite: bool = False):
    with atomic_write(str(path), overwrite=overwrite) as f:
      os.fchmod(f.fileno(), mode)
      f.write(value)

  def _read(self, name: str) -> str | None:
    path = self.root / name
    return path.read_text().strip() if path.is_file() else None

  def local_state(self) -> str:
    state = self._read("state")
    return state if state in ("pending", "paired", "revoked") else "unpaired"

  def ensure_key_pair(self) -> tuple[str, str]:
    self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(self.root, 0o700)
    private_path, public_path = self.root / "id_ecdsa", self.root / "id_ecdsa.pub"

    if not private_path.exists():
      if public_path.exists():
        raise CarrotLinkError("CarrotLink private key is missing")
      key = ECC.generate(curve="P-256")
      self._write(private_path, key.export_key(format="PEM"))
    else:
      os.chmod(private_path, 0o600)
      try:
        key = ECC.import_key(private_path.read_text())
      except (ValueError, IndexError, TypeError) as e:
        raise CarrotLinkError("CarrotLink private key is invalid") from e
    if not key.has_private() or key.curve != "NIST P-256":
      raise CarrotLinkError("CarrotLink private key must be P-256")

    private_key = private_path.read_text()
    public_key = ECC.import_key(private_key).public_key().export_key(format="PEM")
    if public_path.exists():
      try:
        if ECC.import_key(public_path.read_text()).public_key() != ECC.import_key(public_key):
          raise CarrotLinkError("CarrotLink key pair does not match")
      except (ValueError, IndexError, TypeError) as e:
        raise CarrotLinkError("CarrotLink public key is invalid") from e
    else:
      self._write(public_path, public_key, 0o644)
    return private_key, public_key

  def _persistent_uuid(self, name: str) -> str:
    value = self._read(name)
    if value is None:
      value = str(uuid.uuid4())
      self._write(self.root / name, value)
    try:
      return str(uuid.UUID(value))
    except ValueError as e:
      raise CarrotLinkError(f"CarrotLink {name} is invalid") from e

  def _device_id(self) -> str:
    value = self._read("device_id")
    if value is None:
      raise CarrotLinkError("CarrotLink is not registered")
    try:
      return str(uuid.UUID(value))
    except ValueError as e:
      raise CarrotLinkError("CarrotLink device_id is invalid") from e

  @staticmethod
  def _token(identity: str, purpose: str, jti: str, key: str, algorithm: str, extra_claims: dict | None = None) -> str:
    now = datetime.now(UTC)
    claims = {
      "identity": identity,
      "purpose": purpose,
      "jti": jti,
      "iat": now,
      "nbf": now,
      "exp": now + timedelta(seconds=PAIRING_TTL),
    }
    claims.update(extra_claims or {})
    return jwt.encode(
      claims,
      key,
      algorithm=algorithm,
    )

  def _request(self, method: str, path: str, token: str, expected: int, **kwargs):
    try:
      response = self.session.request(method, self.api_host + path, timeout=15, allow_redirects=False, headers={"Authorization": "JWT " + token}, **kwargs)
    except requests.RequestException as e:
      raise CarrotLinkError("CarrotLink is unavailable") from e
    if response.status_code == 410:
      self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
      self._write(self.root / "state", "revoked", overwrite=True)
      raise CarrotLinkError("CarrotLink pairing was revoked")
    if response.status_code != expected:
      raise CarrotLinkError(f"CarrotLink request failed ({response.status_code})")
    return response

  def _bootstrap_identity(self, serial: str) -> tuple[str, str, str]:
    algorithm, private_key, public_key = get_key_pair()
    if algorithm not in ("RS256", "ES256") or not private_key or not public_key:
      raise CarrotLinkError("comma registration key is unavailable")
    token = jwt.encode(
      {"register": True, "exp": datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)}, private_key, algorithm=algorithm
    )
    try:
      response = self.session.request(
        "POST", self.api_host + "/v2/pilotauth/", timeout=15, allow_redirects=False,
        json={"serial": serial, "public_key": public_key, "register_token": token},
      )
    except requests.RequestException as e:
      raise CarrotLinkError("CarrotLink is unavailable") from e
    if response.status_code != 200:
      raise CarrotLinkError(f"CarrotLink bootstrap failed ({response.status_code})")
    try:
      identity = response.json()["dongle_id"]
      if re.fullmatch(r"[0-9a-f]{16}", identity) is None:
        raise ValueError
    except (KeyError, TypeError, ValueError, requests.JSONDecodeError) as e:
      raise CarrotLinkError("CarrotLink bootstrap response is invalid") from e
    return identity, algorithm, private_key

  def register(self, dongle_id: str | None = None, serial: str | None = None) -> str:
    self._require_offroad()
    if self._read("state") == "revoked":
      raise CarrotLinkError("CarrotLink pairing was revoked")
    if self._read("device_id") is not None:
      return self._device_id()

    serial = serial or HARDWARE.get_serial()
    if not serial:
      raise CarrotLinkError("comma registration is unavailable")
    private_key, public_key = self.ensure_key_pair()
    del private_key
    if dongle_id is None:
      dongle_id, algorithm, comma_private_key = self._bootstrap_identity(serial)
    else:
      algorithm, comma_private_key, _ = get_key_pair()
      if algorithm not in ("RS256", "ES256") or not comma_private_key:
        raise CarrotLinkError("comma registration key is unavailable")

    jti = self._persistent_uuid("registration_jti")
    token = self._token(dongle_id, "carrotlink-register", jti, comma_private_key, algorithm)
    response = self._request("POST", "/v1/carrotlink/devices/register", token, 200, json={"public_key": public_key, "serial": serial})
    try:
      device_id = str(uuid.UUID(response.json()["device_id"]))
    except (KeyError, TypeError, ValueError, requests.JSONDecodeError) as e:
      raise CarrotLinkError("CarrotLink registration response is invalid") from e
    self._write(self.root / "device_id", device_id)
    self._write(self.root / "state", "pending", overwrite=True)
    return device_id

  def _device_token(self, device_id: str, purpose: str) -> str:
    private_key, _ = self.ensure_key_pair()
    return self._token(device_id, purpose, str(uuid.uuid4()), private_key, "ES256")

  def connection_url(self) -> str:
    return self.api_host.replace("https://", "wss://", 1) + "/ws/carrotlink"

  def connection_token(self) -> str:
    if self.local_state() != "paired":
      raise CarrotLinkError("CarrotLink is not paired")
    device_id = self._device_id()
    private_key, _ = self.ensure_key_pair()
    return self._token(device_id, "carrotlink-connect", str(uuid.uuid4()), private_key, "ES256", {
      "sub": device_id, "iss": "carrotlink-device", "aud": "carrotlink-runtime",
    })

  def mark_revoked(self):
    self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    self._write(self.root / "state", "revoked", overwrite=True)

  def create_pairing_session(self, dongle_id: str | None = None, serial: str | None = None) -> dict:
    self._require_offroad()
    device_id = self.register(dongle_id, serial)
    token = self._device_token(device_id, "pairing-session")
    response = self._request("POST", f"/v1/carrotlink/devices/{device_id}/pairing-sessions", token, 201)
    try:
      result = response.json()
      session_id = str(uuid.UUID(result["session_id"]))
      pairing_url = result["pairing_url"]
      expires_at = result["expires_at"]
      parsed = urlsplit(pairing_url)
      if re.fullmatch(r"[A-Za-z0-9_-]{43}", parsed.fragment) is None:
        raise ValueError
      code = base64.b64decode(parsed.fragment + "=", altchars=b"-_", validate=True)
      if (
        parsed.scheme != "https"
        or (parsed.hostname, parsed.port or 443) != self.origin
        or parsed.path != "/pair"
        or parsed.query
        or len(parsed.fragment) != 43
        or len(code) != 32
      ):
        raise ValueError
      uuid.UUID(session_id)
      expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
      if expires.tzinfo is None:
        raise ValueError
    except (KeyError, TypeError, ValueError, requests.JSONDecodeError) as e:
      raise CarrotLinkError("CarrotLink pairing response is invalid") from e
    self._write(self.root / "state", "pending", overwrite=True)
    return {"session_id": session_id, "pairing_url": pairing_url, "expires_at": expires_at}

  def pairing_status(self) -> str:
    state = self._read("state")
    if state == "revoked":
      return state
    self._require_offroad()
    device_id = self._device_id()
    token = self._device_token(device_id, "pairing-status")
    response = self._request("GET", f"/v1/carrotlink/devices/{device_id}/pairing", token, 200)
    try:
      status = response.json()["status"]
    except (KeyError, TypeError, requests.JSONDecodeError) as e:
      raise CarrotLinkError("CarrotLink status response is invalid") from e
    if status not in ("pending", "paired", "revoked"):
      raise CarrotLinkError("CarrotLink status response is invalid")
    self._write(self.root / "state", status, overwrite=True)
    return status

  def poll_pairing_status(self, timeout: float = PAIRING_TTL, interval: float = 2) -> str:
    if timeout <= 0 or timeout > PAIRING_TTL or interval <= 0:
      raise ValueError("invalid polling interval")
    deadline = time.monotonic() + timeout
    while True:
      status = self.pairing_status()
      if status != "pending" or time.monotonic() >= deadline:
        return status
      time.sleep(min(interval, max(0, deadline - time.monotonic())))

  def cancel_pairing_session(self, session_id: str):
    self._require_offroad()
    device_id = self._device_id()
    try:
      session_id = str(uuid.UUID(session_id))
    except ValueError as e:
      raise CarrotLinkError("CarrotLink session_id is invalid") from e
    token = self._device_token(device_id, "pairing-session")
    self._request("DELETE", f"/v1/carrotlink/devices/{device_id}/pairing-sessions/{session_id}", token, 204)
    self._write(self.root / "state", "pending", overwrite=True)


def carrotlink_state(persist_root: str | Path | None = None) -> str:
  path = _carrotlink_root(persist_root) / "state"
  try:
    state = path.read_text().strip()
  except OSError:
    return "unpaired"
  return state if state in ("pending", "paired", "revoked") else "unpaired"


def _connection_state(params: Params, metadata) -> dict:
  return {
    "version": metadata.openpilot.version,
    "branch": metadata.channel,
    "onroad": not params.get_bool("IsOffroad"),
  }


def handle_connection_rpc(message: str, state_provider) -> str:
  request_id = None
  try:
    request = json.loads(message)
  except (TypeError, ValueError, RecursionError):
    return json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}, separators=(",", ":"))
  invalid = (
    not isinstance(request, dict)
    or request.get("jsonrpc") != "2.0"
    or "id" not in request
    or request["id"] is None
    or not isinstance(request.get("method"), str)
  )
  if invalid:
    request_id = request.get("id") if isinstance(request, dict) else None
    return json.dumps(
      {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": request_id}, separators=(",", ":")
    )
  request_id = request["id"]
  if request["method"] != "getState":
    return json.dumps({"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": request_id}, separators=(",", ":"))
  if request.get("params") not in (None, {}, []):
    return json.dumps({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params"}, "id": request_id}, separators=(",", ":"))
  try:
    state = state_provider()
  except Exception:
    return json.dumps({"jsonrpc": "2.0", "error": {"code": -32603, "message": "Internal error"}, "id": request_id}, separators=(",", ":"))
  return json.dumps({"jsonrpc": "2.0", "result": state, "id": request_id}, separators=(",", ":"))


def _connection_retry_delay(failures: int, jitter=random.uniform) -> float:
  base = min(CONNECTION_RETRY_MAX, 2 ** min(max(0, failures - 1), 6))
  return base + jitter(0.0, min(1.0, base / 4))


def run_connection(exit_event: threading.Event | None = None, client: CarrotLinkClient | None = None, connector=create_connection, state_provider=None):
  exit_event = exit_event or threading.Event()
  client = client or CarrotLinkClient()
  metadata = get_build_metadata()
  state_provider = state_provider or (lambda: _connection_state(client.params, metadata))
  failures = 0

  while not exit_event.is_set():
    state = client.local_state()
    if state == "revoked":
      exit_event.wait(60)
      continue
    if state != "paired":
      exit_event.wait(5)
      continue

    sock = None
    try:
      token = client.connection_token()
      sock = connector(
        client.connection_url(), header=["Authorization: JWT " + token], timeout=15, enable_multithread=True, redirect_limit=0,
      )
      sock.settimeout(5)
      idle_timeouts = 0
      while not exit_event.is_set():
        try:
          message = sock.recv()
        except WebSocketTimeoutException:
          idle_timeouts += 1
          if idle_timeouts >= CONNECTION_IDLE_TIMEOUTS:
            break
          continue
        if message in (None, ""):
          break
        if not isinstance(message, str):
          continue
        sock.send(handle_connection_rpc(message, state_provider))
        idle_timeouts = 0
        failures = 0
    except WebSocketBadStatusException as e:
      if getattr(e, "status_code", None) == 410:
        client.mark_revoked()
        continue
    except (CarrotLinkError, OSError, WebSocketException):
      pass
    finally:
      if sock is not None:
        try:
          sock.close()
        except WebSocketException:
          pass

    failures += 1
    exit_event.wait(_connection_retry_delay(failures))


def main():
  run_connection()


@dataclass(frozen=True)
class PairingSnapshot:
  state: str
  pairing_url: str | None = None
  error: str | None = None


# ponytail: one device pairing at a time; revisit only if parallel sessions become a real requirement.
_PAIRING_WORKER_LOCK = threading.Lock()


class CarrotLinkPairing:
  """One short-lived pairing worker shared by both comma UIs."""

  def __init__(self, client: CarrotLinkClient | None = None, poll_interval: float = 2.0):
    if poll_interval <= 0:
      raise ValueError("poll_interval must be positive")
    self._client = client
    self._poll_interval = poll_interval
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self._abandon = threading.Event()
    self._thread: threading.Thread | None = None
    self._snapshot = PairingSnapshot("idle")

  def snapshot(self) -> PairingSnapshot:
    with self._lock:
      return self._snapshot

  def _set_snapshot(self, state: str, pairing_url: str | None = None, error: str | None = None):
    with self._lock:
      self._snapshot = PairingSnapshot(state, pairing_url, error)

  def start(self):
    with self._lock:
      if self._thread is not None and self._thread.is_alive():
        return
      self._snapshot = PairingSnapshot("loading")
      self._stop.clear()
      self._abandon.clear()
      self._thread = threading.Thread(target=self._run, daemon=True)
      thread = self._thread
    thread.start()

  def stop(self, cancel: bool = True):
    if not cancel:
      self._abandon.set()
    self._stop.set()

  def _run(self):
    with _PAIRING_WORKER_LOCK:
      if self._stop.is_set():
        return
      self._run_session()

  def _run_session(self):
    client: CarrotLinkClient | None = self._client
    session_id: str | None = None
    pairing_url: str | None = None
    try:
      if self._stop.is_set():
        return
      client = client or CarrotLinkClient()
      result = client.create_pairing_session()
      session_id = result["session_id"]
      pairing_url = result["pairing_url"]
      expires_at = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
      deadline = time.monotonic() + min(PAIRING_TTL, max(0.0, (expires_at - datetime.now(UTC)).total_seconds()))
      self._set_snapshot("pending", pairing_url)

      while not self._stop.is_set():
        state = client.pairing_status()
        if state != "pending":
          self._set_snapshot(state, pairing_url if state == "paired" else None)
          return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          self._set_snapshot("expired")
          return
        if self._stop.wait(min(self._poll_interval, remaining)):
          break
    except CarrotLinkError as e:
      if not self._stop.is_set():
        state = client.local_state() if client is not None else "unpaired"
        self._set_snapshot("revoked" if state == "revoked" else "error", error=None if state == "revoked" else str(e))
    except Exception:
      if not self._stop.is_set():
        self._set_snapshot("error", error="CarrotLink pairing failed")
    finally:
      state = self.snapshot().state
      if (
        not self._abandon.is_set()
        and client is not None
        and session_id is not None
        and state not in ("paired", "revoked")
        and (self._stop.is_set() or state in ("expired", "error"))
      ):
        try:
          client.cancel_pairing_session(session_id)
        except CarrotLinkError:
          try:
            reconciled = client.pairing_status()
            if reconciled != "pending":
              self._set_snapshot(reconciled, pairing_url if reconciled == "paired" else None)
          except CarrotLinkError:
            pass
