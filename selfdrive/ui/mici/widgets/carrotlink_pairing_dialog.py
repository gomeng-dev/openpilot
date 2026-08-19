import pyray as rl
from collections.abc import Callable

from openpilot.selfdrive.carrot.carrotlink import CarrotLinkPairing
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.widgets.pairing_dialog import PairingDialog
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.widgets.nav_widget import NavWidget


class CarrotLinkPairingDialog(PairingDialog):
  """mici CarrotLink QR flow using the existing QR renderer."""

  def __init__(self, state_callback: Callable[[str], None] | None = None):
    super().__init__()
    self._carrotlink = CarrotLinkPairing()
    self._state_callback = state_callback
    self._pairing_url = ""
    self._pairing_state = "idle"
    self._completed = False
    self._pair_label.set_text("pair with carrotlink")

  def _get_pairing_url(self) -> str:
    return self._pairing_url

  def _check_qr_refresh(self) -> None:
    pass

  def _clear_qr_texture(self):
    if self._qr_texture and self._qr_texture.id != 0:
      rl.unload_texture(self._qr_texture)
    self._qr_texture = None

  def _update_state(self):
    NavWidget._update_state(self)
    if not ui_state.is_offroad():
      self._carrotlink.stop(cancel=False)
      if not self.is_dismissing:
        self.dismiss()
      return
    snapshot = self._carrotlink.snapshot()
    self._pairing_state = snapshot.state
    if snapshot.pairing_url != self._pairing_url:
      self._pairing_url = snapshot.pairing_url or ""
      if self._pairing_url:
        self._generate_qr_code()
      else:
        self._clear_qr_texture()

    if snapshot.state in ("paired", "revoked") and not self._completed:
      self._completed = True
      if self._state_callback is not None:
        self._state_callback(snapshot.state)
      if snapshot.state == "paired":
        self.dismiss()

  def show_event(self):
    super().show_event()
    self._carrotlink.start()

  def hide_event(self):
    self._carrotlink.stop()
    super().hide_event()

  def _render_qr_code(self) -> None:
    if self._qr_texture:
      super()._render_qr_code()
      return

    status = {
      "idle": "starting...",
      "loading": "connecting...",
      "expired": "pairing expired",
      "revoked": "pairing revoked",
      "error": "pairing failed",
    }.get(self._pairing_state, "QR Code Error")
    color = rl.RED if self._pairing_state in ("expired", "revoked", "error") else rl.WHITE
    rl.draw_text_ex(gui_app.font(FontWeight.BOLD), status, rl.Vector2(self._rect.x + 20, self._rect.y + self._rect.height // 2 - 15), 30, 0.0, color)
