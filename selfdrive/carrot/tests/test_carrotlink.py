import base64
import importlib.util
import os
import stat
import sys
import types
import uuid
from contextlib import contextmanager
from pathlib import Path

import jwt
from Crypto.PublicKey import ECC, RSA


@contextmanager
def atomic_write(path, mode="w", overwrite=False, **_kwargs):
  if not overwrite and os.path.exists(path):
    raise FileExistsError(path)
  tmp = str(path) + ".tmp"
  with open(tmp, mode) as f:
    yield f
  os.replace(tmp, path)


class FakeParams:
  def __init__(self, offroad=True):
    self.values = {"IsOffroad": offroad, "DongleId": "comma-id"}

  def get_bool(self, key):
    return bool(self.values.get(key))

  def get(self, key):
    return self.values.get(key)


class FakeResponse:
  def __init__(self, status_code, data=None):
    self.status_code = status_code
    self.data = data

  def json(self):
    return self.data


class FakeSession:
  def __init__(self, responses):
    self.responses = iter(responses)
    self.calls = []

  def request(self, method, url, **kwargs):
    self.calls.append((method, url, kwargs))
    return next(self.responses)


def load_module(tmp_path, monkeypatch):
  rsa = RSA.generate(2048)
  comma_private = rsa.export_key().decode()

  modules = {
    "openpilot.common.api": types.SimpleNamespace(get_key_pair=lambda: ("RS256", comma_private, rsa.public_key().export_key().decode())),
    "openpilot.common.params": types.SimpleNamespace(Params=FakeParams),
    "openpilot.common.utils": types.SimpleNamespace(atomic_write=atomic_write),
    "openpilot.system.hardware": types.SimpleNamespace(HARDWARE=types.SimpleNamespace(get_serial=lambda: "SERIAL001")),
    "openpilot.system.hardware.hw": types.SimpleNamespace(Paths=types.SimpleNamespace(persist_root=lambda: str(tmp_path))),
  }
  for name, module in modules.items():
    monkeypatch.setitem(sys.modules, name, module)

  path = Path(__file__).parents[1] / "carrotlink.py"
  spec = importlib.util.spec_from_file_location("carrotlink_under_test", path)
  assert spec is not None
  module = importlib.util.module_from_spec(spec)
  assert spec.loader is not None
  spec.loader.exec_module(module)
  return module


def test_pairing_client_contract(tmp_path, monkeypatch):
  module = load_module(tmp_path, monkeypatch)
  device_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
  code = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()
  session = FakeSession(
    [
      FakeResponse(200, {"device_id": device_id}),
      FakeResponse(201, {"session_id": session_id, "pairing_url": f"https://dashboard.example/pair#{code}", "expires_at": "2030-01-01T00:00:00Z"}),
      FakeResponse(200, {"status": "pending"}),
      FakeResponse(200, {"status": "paired"}),
      FakeResponse(204),
    ]
  )
  client = module.CarrotLinkClient("https://Dashboard.Example:443", tmp_path, FakeParams(), session)

  result = client.create_pairing_session(serial="SERIAL001")
  assert result["pairing_url"].endswith("#" + code)
  monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
  assert client.poll_pairing_status(timeout=1, interval=0.01) == "paired"
  client.cancel_pairing_session(session_id)

  key_dir = tmp_path / "carrotlink"
  private_key = key_dir / "id_ecdsa"
  assert stat.S_IMODE(key_dir.stat().st_mode) == 0o700
  assert stat.S_IMODE(private_key.stat().st_mode) == 0o600
  original = private_key.read_bytes()
  client.ensure_key_pair()
  assert private_key.read_bytes() == original

  wrong_curve = module.CarrotLinkClient("https://dashboard.example", tmp_path / "wrong-curve", FakeParams(), FakeSession([]))
  wrong_curve.root.mkdir(parents=True)
  (wrong_curve.root / "id_ecdsa").write_text(ECC.generate(curve="P-384").export_key(format="PEM"))
  try:
    wrong_curve.ensure_key_pair()
  except module.CarrotLinkError as e:
    assert "P-256" in str(e)
  else:
    raise AssertionError("P-384 key was accepted")

  register_claims = jwt.decode(session.calls[0][2]["headers"]["Authorization"].removeprefix("JWT "), options={"verify_signature": False})
  assert register_claims["identity"] == "comma-id"
  assert register_claims["purpose"] == "carrotlink-register"
  assert register_claims["jti"] == (key_dir / "registration_jti").read_text()
  assert 0 < register_claims["exp"] - register_claims["iat"] <= module.PAIRING_TTL

  session_claims = jwt.decode(session.calls[1][2]["headers"]["Authorization"].removeprefix("JWT "), options={"verify_signature": False})
  assert session_claims["identity"] == device_id
  assert session_claims["purpose"] == "pairing-session"
  jwt.decode(
    session.calls[1][2]["headers"]["Authorization"].removeprefix("JWT "),
    (key_dir / "id_ecdsa.pub").read_text(),
    algorithms=["ES256"],
  )
  assert all(call[2]["allow_redirects"] is False for call in session.calls)
  assert session.calls[-1][0] == "DELETE"
  assert session.calls[-1][1].endswith("/pairing-sessions/" + session_id)
  assert client._read("state") == "pending"


def test_revoked_state_stops_network_and_offroad_gate(tmp_path, monkeypatch):
  module = load_module(tmp_path, monkeypatch)
  session = FakeSession([])
  client = module.CarrotLinkClient("https://dashboard.example", tmp_path, FakeParams(), session)
  client.root.mkdir(parents=True)
  (client.root / "state").write_text("revoked")

  assert client.pairing_status() == "revoked"
  assert session.calls == []
  try:
    client.register()
  except module.CarrotLinkError as e:
    assert "revoked" in str(e)
  else:
    raise AssertionError("revoked registration succeeded")

  onroad = module.CarrotLinkClient("https://dashboard.example", tmp_path / "onroad", FakeParams(False), session)
  onroad.root.mkdir(parents=True)
  (onroad.root / "device_id").write_text(str(uuid.uuid4()))
  try:
    onroad.pairing_status()
  except module.CarrotLinkError as e:
    assert "offroad" in str(e)
  else:
    raise AssertionError("onroad status request succeeded")
  assert session.calls == []


def test_registration_retry_reuses_assertion(tmp_path, monkeypatch):
  module = load_module(tmp_path, monkeypatch)
  device_id = str(uuid.uuid4())
  session = FakeSession([FakeResponse(500), FakeResponse(200, {"device_id": device_id})])
  client = module.CarrotLinkClient("https://dashboard.example", tmp_path, FakeParams(), session)

  try:
    client.register(serial="SERIAL001")
  except module.CarrotLinkError:
    pass
  else:
    raise AssertionError("failed registration succeeded")
  assert client.register(serial="SERIAL001") == device_id

  claims = [jwt.decode(call[2]["headers"]["Authorization"].removeprefix("JWT "), options={"verify_signature": False}) for call in session.calls]
  assert claims[0]["jti"] == claims[1]["jti"]


def test_gone_response_persists_revocation(tmp_path, monkeypatch):
  module = load_module(tmp_path, monkeypatch)
  device_id = str(uuid.uuid4())
  session = FakeSession([FakeResponse(410)])
  client = module.CarrotLinkClient("https://dashboard.example", tmp_path, FakeParams(), session)
  client.root.mkdir(parents=True)
  (client.root / "device_id").write_text(device_id)

  try:
    client.create_pairing_session()
  except module.CarrotLinkError as e:
    assert "revoked" in str(e)
  else:
    raise AssertionError("revoked pairing succeeded")
  assert client.pairing_status() == "revoked"
  assert len(session.calls) == 1
