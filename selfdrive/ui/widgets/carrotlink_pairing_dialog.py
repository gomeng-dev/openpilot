import pyray as rl
from collections.abc import Callable

from openpilot.selfdrive.carrot.carrotlink import CarrotLinkPairing
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.widgets.pairing_dialog import PairingDialog
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.wrap_text import wrap_text


class CarrotLinkPairingDialog(PairingDialog):
  """CarrotLink's one-time QR flow on top of the existing QR renderer."""

  def __init__(self, state_callback: Callable[[str], None] | None = None):
    super().__init__()
    self._carrotlink = CarrotLinkPairing()
    self._state_callback = state_callback
    self._pairing_url = ""
    self._pairing_state = "idle"
    self._completed = False

  def _get_pairing_url(self) -> str:
    return self._pairing_url

  def _check_qr_refresh(self) -> None:
    # The backend owns the five-minute session; never mint a QR on the UI thread.
    pass

  def _clear_qr_texture(self):
    if self.qr_texture and self.qr_texture.id != 0:
      rl.unload_texture(self.qr_texture)
    self.qr_texture = None

  def _update_state(self):
    if not ui_state.is_offroad():
      self._carrotlink.stop(cancel=False)
      gui_app.pop_widget()
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
        gui_app.pop_widget()

  def show_event(self):
    super().show_event()
    self._carrotlink.start()

  def hide_event(self):
    self._carrotlink.stop()
    super().hide_event()

  def _render(self, rect: rl.Rectangle) -> int:
    rl.clear_background(rl.Color(224, 224, 224, 255))

    margin = 70
    content_rect = rl.Rectangle(rect.x + margin, rect.y + margin, rect.width - 2 * margin, rect.height - 2 * margin)
    y = content_rect.y

    close_size = 80
    pad = 20
    self._close_btn.render(rl.Rectangle(content_rect.x - pad, y - pad, close_size + pad * 2, close_size + pad * 2))
    y += close_size + 40

    title_font = gui_app.font(FontWeight.NORMAL)
    left_width = int(content_rect.width * 0.5 - 15)
    title_wrapped = wrap_text(title_font, tr("Pair your device with CarrotLink"), 75, left_width)
    rl.draw_text_ex(title_font, "\n".join(title_wrapped), rl.Vector2(content_rect.x, y), 75, 0.0, rl.BLACK)
    y += len(title_wrapped) * 75 + 60

    remaining_height = content_rect.height - (y - content_rect.y)
    right_width = content_rect.width // 2 - 20
    self._render_instructions(rl.Rectangle(content_rect.x, y, left_width, remaining_height))

    qr_size = min(right_width, content_rect.height) - 40
    qr_x = content_rect.x + left_width + 40 + (right_width - qr_size) // 2
    self._render_qr_code(rl.Rectangle(qr_x, content_rect.y, qr_size, qr_size))
    return -1

  def _render_instructions(self, rect: rl.Rectangle) -> None:
    instructions = [
      tr("Open the camera on your phone"),
      tr("Scan the QR code on the right"),
      tr("Sign in to CommaLink and approve this device"),
    ]
    font = gui_app.font(FontWeight.BOLD)
    y = rect.y

    for i, text in enumerate(instructions):
      circle_radius = 25
      circle_x = rect.x + circle_radius + 15
      text_x = rect.x + circle_radius * 2 + 40
      wrapped = wrap_text(font, text, 47, int(rect.width - (circle_radius * 2 + 40)))
      text_height = len(wrapped) * 47
      circle_y = y + text_height // 2
      rl.draw_circle(int(circle_x), int(circle_y), circle_radius, rl.Color(70, 70, 70, 255))
      number = str(i + 1)
      number_size = measure_text_cached(font, number, 30)
      rl.draw_text_ex(font, number, (int(circle_x - number_size.x // 2), int(circle_y - number_size.y // 2)), 30, 0, rl.WHITE)
      rl.draw_text_ex(font, "\n".join(wrapped), rl.Vector2(text_x, y), 47, 0.0, rl.BLACK)
      y += text_height + 50

  def _render_qr_code(self, rect: rl.Rectangle) -> None:
    if self.qr_texture:
      super()._render_qr_code(rect)
      return

    rl.draw_rectangle_rounded(rect, 0.1, 20, rl.Color(240, 240, 240, 255))
    status = {
      "idle": tr("Starting…"),
      "loading": tr("Connecting…"),
      "expired": tr("Pairing code expired. Close and try again."),
      "revoked": tr("CarrotLink pairing was revoked."),
      "error": tr("CarrotLink pairing failed."),
    }.get(self._pairing_state, tr("QR Code Error"))
    color = rl.RED if self._pairing_state in ("expired", "revoked", "error") else rl.Color(70, 70, 70, 255)
    rl.draw_text_ex(gui_app.font(FontWeight.BOLD), status, rl.Vector2(rect.x + 20, rect.y + rect.height // 2 - 15), 30, 0.0, color)
