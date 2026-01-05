"""
Taken from: https://github.com/TomJGooding/textual-slider.
Adds a few typing tweaks and support for float values to the original
implementation.

@date: 05.01.2025
@author: Baptiste Pestourie
"""

from __future__ import annotations

from math import ceil
from typing import Optional

from rich.console import RenderableType
from textual import events
from textual.binding import Binding
from textual.geometry import Offset, clamp
from textual.message import Message
from textual.reactive import reactive, var
from textual.scrollbar import ScrollBarRender
from textual.widget import Widget

DEFAULT_RESOLUTION = 30

VSIZE = 100


class Slider[T: (int, float)](Widget, can_focus=True):
    """A simple slider widget."""

    BINDINGS = [
        Binding("right", "slide_right", "Slide Right", show=False),
        Binding("left", "slide_left", "Slide Left", show=False),
    ]

    COMPONENT_CLASSES = {"slider--slider"}

    DEFAULT_CSS = """
    Slider {
        width: 32;
        height: 3;
        min-height: 3;
        border: tall $border-blurred;
        background: $surface;
        padding: 0 2;

        & > .slider--slider {
            background: $panel-darken-2;
            color: $primary;
        }

        &:focus {
            border: tall $border;
            background-tint: $foreground 5%;
        }
    }
    """
    value_type: type[T]
    value: reactive[T] = reactive(0, init=False)  # type: ignore[arg-type]
    """The value of the slider."""

    _slider_position: reactive[float] = reactive(0.0)
    _grabbed: var[Offset | None] = var[Optional[Offset]](None)
    _grabbed_position: var[float] = var(0.0)

    class Changed(Message):
        """Posted when the value of the slider changes.

        This message can be handled using an `on_slider_changed` method.
        """

        def __init__(self, slider: Slider, value: int | float) -> None:
            super().__init__()
            self.value = value
            self.slider: Slider = slider

        @property
        def control(self) -> Slider:
            return self.slider

    def __init__(
        self,
        min: T,
        max: T,
        *,
        resolution: int | None = None,
        value_type: type[T] = int,  # type: ignore[assignment]
        value: T | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        """Create a slider widget.

        Args:
            min: The minimum value of the slider.
            max: The maximum value of the slider.
            step: The step size of the slider.
            value: The initial value of the slider.
            name: The name of the slider.
            id: The ID of the slider in the DOM.
            classes: The CSS classes of the slider.
            disabled: Whether the slider is disabled or not.
        """
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self.min: T = min
        self.max: T = max
        self.value_type: type[T] = value_type
        self.resolution = resolution or DEFAULT_RESOLUTION
        self.value = value if value is not None else min
        self._slider_position = (
            (self.value - self.min) / (self.resolution / VSIZE)
        ) / self.step

    @property
    def range(self) -> T:
        return self.max - self.min

    @property
    def step(self) -> float:
        return self.range / self.resolution

    @property
    def is_float(self) -> bool:
        return self.value_type is float

    def validate_value(self, value: T) -> T:
        return clamp(value, self.min, self.max)

    def validate__slider_position(self, slider_position: float) -> float:
        max_position = ((self.max - self.min) / (self.resolution / VSIZE)) / self.step
        return clamp(slider_position, 0, max_position)

    def watch_value(self) -> None:
        if not self._grabbed:
            delta = self.value - self.min
            self._slider_position = (delta / (self.resolution / VSIZE)) / self.step
        self.post_message(self.Changed(self, self.value))

    def render(self) -> RenderableType:
        style = self.get_component_rich_style("slider--slider")
        step_ratio = ceil(VSIZE / self.resolution)
        return ScrollBarRender(
            virtual_size=VSIZE,
            window_size=step_ratio,
            position=self._slider_position,
            style=style,
            vertical=False,
        )

    def action_slide_right(self) -> None:
        value = self.value + self.step
        self.value = value if self.is_float else round(value)  # type: ignore[assignment]

    def action_slide_left(self) -> None:
        value = self.value - self.step
        self.value = value if self.is_float else round(value)  # type: ignore[assignment]

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        event.stop()

        mouse_x = event.x - self.styles.gutter.left
        mouse_y = event.y - self.styles.gutter.top

        if not (0 <= mouse_x < self.content_size.width) or not (
            0 <= mouse_y < self.content_size.height
        ):
            return

        step_ratio = ceil(VSIZE / self.resolution)
        thumb_size = max(1, step_ratio / (VSIZE / self.content_size.width))

        self._slider_position = (
            (mouse_x - (thumb_size // 2)) / self.content_size.width
        ) * VSIZE

        self._grabbed = event.screen_offset
        self.action_grab()

        value = self.step * self._slider_position * (self.resolution / VSIZE) + self.min
        self.value = value if self.is_float else round(value)  # type: ignore[assignment]

    def action_grab(self) -> None:
        self.capture_mouse()
        # Workaround for unexpected mouse grab and drag behaviour
        # depending on the currently focused widget.
        # Stolen from https://github.com/1j01/textual-paint
        self.can_focus = False

    async def _on_mouse_up(self, event: events.MouseUp) -> None:
        if self._grabbed:
            self.release_mouse()
            self._grabbed = None

            # Workaround for unexpected mouse behaviour mentioned above
            self.can_focus = True

        event.stop()

    def _on_mouse_capture(self, event: events.MouseCapture) -> None:
        self._grabbed = event.mouse_position
        self._grabbed_position = self._slider_position

    def _on_mouse_release(self, event: events.MouseRelease) -> None:
        self._grabbed = None
        event.stop()

    async def _on_mouse_move(self, event: events.MouseMove) -> None:
        if self._grabbed:
            mouse_move = event.screen_x - self._grabbed.x
            self._slider_position = self._grabbed_position + (
                mouse_move * (VSIZE / self.content_size.width)
            )
            value = (
                self.step * round(self._slider_position * (self.resolution / VSIZE))
                + self.min
            )
            self.value = value if self.is_float else round(value)  # type: ignore[assignment]

        event.stop()

    async def _on_click(self, event: events.Click) -> None:
        event.stop()
