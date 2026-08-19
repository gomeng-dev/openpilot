import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class Snapshot:
  state: str
  pairing_url: str | None = None
  error: str | None = None


class FakeWorker:
  def __init__(self):
    self.value = Snapshot("idle")
    self.started = False
    self.stopped = False
    self.abandoned = False

  def start(self):
    self.started = True

  def stop(self, cancel=True):
    self.stopped = True
    self.abandoned |= not cancel

  def snapshot(self):
    return self.value


class FakeTexture:
  id = 1
  width = 100
  height = 100


class FakeLabel:
  def set_text(self, text):
    self.text = text


class FakePairingDialog:
  def __init__(self):
    self.qr_texture = None
    self._qr_texture = None
    self._pair_label = FakeLabel()
    self.dismissed = False
    self.is_dismissing = False

  def _generate_qr_code(self):
    self.generated_url = self._get_pairing_url()
    texture = FakeTexture()
    self.qr_texture = texture
    self._qr_texture = texture

  def _render_qr_code(self, *_args):
    pass

  def show_event(self):
    pass

  def hide_event(self):
    pass

  def dismiss(self):
    self.dismissed = True


class FakeNavWidget:
  def _update_state(self):
    self.nav_updated = True


class FakeGuiApp:
  def __init__(self):
    self.popped = False

  def pop_widget(self):
    self.popped = True

  def font(self, _weight):
    return object()


class FakeUIState:
  def __init__(self):
    self.offroad = True

  def is_offroad(self):
    return self.offroad


class FakeRl(types.ModuleType):
  class Rectangle:
    pass

  class Vector2:
    def __init__(self, *_args):
      pass

  class Color:
    def __init__(self, *_args):
      pass

  RED = Color()
  WHITE = Color()
  BLACK = Color()

  @staticmethod
  def unload_texture(_texture):
    pass


def _module(name, **attrs):
  module = types.ModuleType(name)
  for key, value in attrs.items():
    setattr(module, key, value)
  return module


def _load_dialog(monkeypatch, mici: bool):
  gui_app = FakeGuiApp()
  ui_state = FakeUIState()
  fake_rl = FakeRl("pyray")
  monkeypatch.setitem(sys.modules, "pyray", fake_rl)
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.carrot.carrotlink", _module("carrotlink", CarrotLinkPairing=FakeWorker))
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.ui.ui_state", _module("ui_state", ui_state=ui_state))
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.ui.widgets.pairing_dialog", _module("pairing_dialog", PairingDialog=FakePairingDialog))
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.ui.mici.widgets.pairing_dialog", _module("mici_pairing_dialog", PairingDialog=FakePairingDialog))
  monkeypatch.setitem(sys.modules, "openpilot.system.ui.widgets.nav_widget", _module("nav_widget", NavWidget=FakeNavWidget))
  monkeypatch.setitem(
    sys.modules, "openpilot.system.ui.lib.application", _module("application", FontWeight=types.SimpleNamespace(NORMAL=0, BOLD=1), gui_app=gui_app)
  )
  monkeypatch.setitem(sys.modules, "openpilot.system.ui.lib.multilang", _module("multilang", tr=lambda text: text))
  monkeypatch.setitem(
    sys.modules, "openpilot.system.ui.lib.text_measure", _module("text_measure", measure_text_cached=lambda *_args: types.SimpleNamespace(x=0, y=0))
  )
  monkeypatch.setitem(sys.modules, "openpilot.system.ui.lib.wrap_text", _module("wrap_text", wrap_text=lambda *_args: []))

  root = Path(__file__).parents[3]
  relative = "selfdrive/ui/mici/widgets/carrotlink_pairing_dialog.py" if mici else "selfdrive/ui/widgets/carrotlink_pairing_dialog.py"
  name = f"carrotlink_pairing_dialog_{'mici' if mici else 'tici'}_test"
  spec = importlib.util.spec_from_file_location(name, root / relative)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module, gui_app, ui_state


@pytest.mark.parametrize("mici", [False, True])
def test_carrotlink_dialog_reuses_qr_renderer_and_worker(monkeypatch, mici):
  module, gui_app, ui_state = _load_dialog(monkeypatch, mici)
  completed = []
  dialog = module.CarrotLinkPairingDialog(completed.append)
  worker = dialog._carrotlink

  dialog.show_event()
  assert worker.started

  pairing_url = "https://commalink.gomeng-dev.com/pair#" + "A" * 43
  worker.value = Snapshot("pending", pairing_url)
  dialog._update_state()
  assert dialog.generated_url == pairing_url

  worker.value = Snapshot("expired")
  dialog._update_state()
  texture = dialog._qr_texture if mici else dialog.qr_texture
  assert texture is None

  worker.value = Snapshot("paired", pairing_url)
  dialog._update_state()
  dialog._update_state()
  assert completed == ["paired"]
  assert dialog.dismissed if mici else gui_app.popped

  revoked = []
  gui_app.popped = False
  revoked_dialog = module.CarrotLinkPairingDialog(revoked.append)
  revoked_dialog._carrotlink.value = Snapshot("revoked")
  revoked_dialog._update_state()
  revoked_dialog._update_state()
  assert revoked == ["revoked"]
  assert not (revoked_dialog.dismissed if mici else gui_app.popped)

  onroad_dialog = module.CarrotLinkPairingDialog()
  onroad_dialog.show_event()
  ui_state.offroad = False
  onroad_dialog._update_state()
  onroad_dialog.hide_event()
  assert onroad_dialog._carrotlink.stopped
  assert onroad_dialog._carrotlink.abandoned
  assert onroad_dialog.dismissed if mici else gui_app.popped

  dialog.hide_event()
  assert worker.stopped
