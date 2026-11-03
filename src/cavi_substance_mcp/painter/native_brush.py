"""Native macOS Painter brush input, evaluated inside Painter's main thread.

Qt prepares the paint target; AppKit delivers real window mouse events. Each
gesture is finite, with a release in finally. No undocumented painting symbols
or arbitrary caller-supplied code are used.
"""

import ctypes as C
import math
import secrets
import sys
import time

STATE_ATTR = '_cavi_mcp_brush_v3'


def check_settings(value):
    limits = {'size': (0.01, 1000), 'flow': (0, 100),
              'opacity': (0, 100), 'spacing': (1, 1000)}
    if not isinstance(value, dict) or not set(value) <= limits.keys():
        raise ValueError('Brush settings: size, flow, opacity, spacing')
    for key, number in value.items():
        low, high = limits[key]
        if (type(number) not in (float, int) or not math.isfinite(number)
                or not low <= number <= high
                or key != 'size' and int(number) != number):
            raise ValueError(f'Invalid brush {key}: expected {low}..{high}')
    return value


def points(value):
    if not isinstance(value, (list, tuple)) or not 2 <= len(value) <= 128:
        raise ValueError('A stroke needs 2 to 128 [x, y, pressure] points')
    result = []
    for point in value:
        if (not isinstance(point, (list, tuple)) or len(point) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)
                or not all(0 < v < 1 for v in point[:2]) or not 0 <= point[2] <= 1):
            raise ValueError('Stroke coordinates must be inside (0, 1), pressure in [0, 1]')
        result.append(tuple(float(v) for v in point))
    return result


def gesture(path, send, guard):
    """One stroke, including cleanup after an uncertain partial mutation."""
    guard()
    last = path[0]
    try:
        send(2, last)  # Clear any stale left-button state before pressing.
        guard()
        send(1, last)
        for point in path[1:]:
            guard()
            last = point
            send(6, point)
    except Exception as exc:
        raise RuntimeError(
            f'Brush outcome uncertain; inspect before another stroke: {exc}') from exc
    finally:
        try:
            send(2, last)
        except Exception as exc:
            raise RuntimeError(f'Brush outcome uncertain; mouse release failed: {exc}') from exc


def _qt():
    if sys.platform != 'darwin':
        raise RuntimeError('Native brush input currently requires macOS Painter')
    import shiboken6
    from PySide6 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets, shiboken6


def _widgets(qt, name):
    _, _, widgets, shiboken = qt
    result = []
    for widget in widgets.QApplication.allWidgets():
        try:
            if shiboken.isValid(widget) and widget.isVisible() and widget.objectName() == name:
                result.append(widget)
        except RuntimeError:
            continue
    if len(result) != 1:
        raise RuntimeError(f'Expected one visible Painter {name}; found {len(result)}')
    return result[0]


def settings(qt, values=None):
    core = qt[0]
    bar = _widgets(qt, 'paint_editor_toolbar_current_tool')
    for key, value in check_settings(values or {}).items():
        kind = float if key == 'size' else int
        if not core.QMetaObject.invokeMethod(bar, f'on_{key}_valueChanged',
                                             core.Qt.DirectConnection,
                                             core.Q_ARG(kind, kind(value))):
            raise RuntimeError(f'Painter did not accept brush {key}')
    _frame(qt)
    result = {}
    # Keep all wrappers alive: Painter's custom widget wrappers otherwise lose
    # subclass metadata while walking individual QObject parents.
    widgets = qt[2].QApplication.allWidgets()
    for widget in widgets:
        if not qt[3].isValid(widget) or not isinstance(widget, qt[2].QLineEdit):
            continue
        parent = widget.parent()
        control = None
        while parent is not None and parent != bar:
            if parent.metaObject().className() == 'Alg::Slider':
                control = parent.objectName()
            parent = parent.parent()
        if parent == bar and control in {'size', 'flow', 'opacity', 'spacing'}:
            result[control] = float(widget.text())
    if values and any(key not in result or abs(result[key] - value) > 0.01
                      for key, value in values.items()):
        raise RuntimeError(f'Brush control readback differs from requested values: {result}')
    return result


def apply_preset(qt, url):
    import json

    from substance_painter import js, resource

    core, gui, widgets, shiboken = qt
    matches = [r for r in resource.search('u:brush') if r.identifier().url() == url]
    if len(matches) != 1:
        raise ValueError('Choose an exact brush URL from inspect shelf usage=brush')
    item = matches[0]
    all_widgets = widgets.QApplication.allWidgets()
    for widget in all_widgets:
        if (shiboken.isValid(widget) and isinstance(widget, widgets.QDockWidget)
                and widget.objectName() == 'NewShelf'):
            widget.raise_()
    js.evaluate('alg.resources.selectResources(' + json.dumps([url]) + ')')
    _frame(qt)
    view = _widgets(qt, 'resources')
    selected = view.selectionModel().selectedIndexes()
    if len(selected) != 1 or selected[0].data() != item.gui_name():
        raise RuntimeError('Painter did not select the requested brush resource')
    view.scrollTo(selected[0])
    _frame(qt)
    position = view.visualRect(selected[0]).center()
    global_position = view.viewport().mapToGlobal(position)
    for kind in (core.QEvent.MouseButtonPress, core.QEvent.MouseButtonRelease,
                 core.QEvent.MouseButtonDblClick, core.QEvent.MouseButtonRelease):
        widgets.QApplication.sendEvent(view.viewport(), gui.QMouseEvent(
            kind, core.QPointF(position), core.QPointF(global_position), core.Qt.LeftButton,
            core.Qt.NoButton if kind == core.QEvent.MouseButtonRelease else core.Qt.LeftButton,
            core.Qt.NoModifier))
    _frame(qt)
    return {'url': url, 'name': item.gui_name()}


def _inside(widget, ancestor):
    parent = widget
    while parent is not None:
        if parent == ancestor:
            return True
        parent = parent.parent()
    return False


def _click(qt, window, native, widget):
    parent = widget.parent()
    while parent is not None:
        if isinstance(parent, qt[2].QScrollArea):
            parent.ensureWidgetVisible(widget)
        parent = parent.parent()
    native.activate()
    _frame(qt)
    position = widget.mapTo(window, widget.rect().center())
    local = Point(position.x(), position.y() if native.flipped else window.height()-position.y())
    position = native.send(native.view, 'convertPoint:toView:', Point,
                           (Point, C.c_void_p), (local, None))
    try:
        native.event(1, position, 1)
    finally:
        native.event(2, position, 0)
    _frame(qt)


def _color(qt, window, native, source, value):
    import colorsys

    widgets = qt[2].QApplication.allWidgets()
    buttons = [w for w in widgets if qt[3].isValid(w)
               and w.metaObject().className() == 'Alg::ColorButton' and _inside(w, source)]
    if len(buttons) != 1:
        raise RuntimeError('This channel needs a uniform color source')
    _click(qt, window, native, buttons[0])
    popup = _widgets(qt, 'ColorWidget')
    try:
        widgets = qt[2].QApplication.allWidgets()
        spins = {w.objectName(): w for w in widgets if qt[3].isValid(w)
                 and isinstance(w, qt[2].QDoubleSpinBox) and _inside(w, popup)}
        if set(spins) != {'spinBox1', 'spinBox2', 'spinBox3'}:
            raise RuntimeError('Painter HSV color controls changed')
        # The HSV fields default to three display decimals. Increase their input
        # precision before editing instead of routing through the 8-bit hex field.
        hsv = colorsys.rgb_to_hsv(*value)
        for index, number in enumerate(hsv, 1):
            spin = spins[f'spinBox{index}']
            if not spin.minimum() <= number <= spin.maximum():
                raise ValueError('Color exceeds the native Painter color-control range')
        blocked = {name: spin.blockSignals(True) for name, spin in spins.items()}
        try:
            for index, number in enumerate(hsv, 1):
                spin = spins[f'spinBox{index}']
                spin.setDecimals(8)
                spin.setValue(number)
        finally:
            for name, spin in spins.items():
                spin.blockSignals(blocked[name])
        # Commit all components together. Painter refreshes the fields to three
        # display decimals after a change; sequential edits would re-read rounded
        # H/S values while committing V and lose source precision.
        spins['spinBox3'].valueChanged.emit(hsv[2])
        _frame(qt)
        actual = [spins[f'spinBox{i}'].value() for i in range(1, 4)]
        if any(abs(a-b) > 0.00051 for a, b in zip(actual, hsv, strict=True)):
            raise RuntimeError(f'Painter color readback {actual} differs from requested HSV {hsv}')
        return {'requested_rgb': list(value), 'hsv_display': actual}
    finally:
        if qt[3].isValid(popup):
            popup.close()
        _frame(qt)


def channel_controls(qt, window, native, values, toggles):
    from substance_painter import textureset

    values, toggles = values or {}, toggles or {}
    if not values and not toggles:
        return {}
    widgets = qt[2].QApplication.allWidgets()
    buttons = {}
    for key in set(values) | set(toggles):
        channel = getattr(textureset.ChannelType, key, None)
        if channel is None:
            raise ValueError(f'Unknown Painter channel: {key}')
        matching = [w for w in widgets if qt[3].isValid(w) and w.isVisible()
                    and w.objectName() == 'channelSelector'
                    and isinstance(w, qt[2].QAbstractButton)
                    and w.property('channel') == channel.value]
        if len(matching) != 1:
            raise RuntimeError(f'Expected one active brush control for {key}')
        buttons[key] = matching[0]
    result = {}
    for key, value in values.items():
        button = buttons[key]
        if not button.isChecked():
            button.click()
            _frame(qt)
        widgets = qt[2].QApplication.allWidgets()
        sources = []
        for widget in widgets:
            if (not qt[3].isValid(widget) or not widget.isVisible()
                    or widget.objectName() != 'SplitSourceViews'):
                continue
            layout = widget.layout()
            if not isinstance(layout, qt[2].QGridLayout):
                continue
            for index in range(layout.count()):
                label = layout.itemAt(index).widget()
                if isinstance(label, qt[2].QLabel) and label.text() == button.toolTip():
                    row = layout.getItemPosition(index)[0]
                    sources.append(layout.itemAtPosition(row, 1).widget())
        if len(sources) != 1:
            raise RuntimeError(f'Expected one uniform brush source for {key}')
        source = sources[0]
        if isinstance(value, (list, tuple)):
            result[key] = _color(qt, window, native, source, value)
        else:
            editors = [w for w in widgets if qt[3].isValid(w)
                       and isinstance(w, qt[2].QLineEdit) and w.objectName() == 'value'
                       and _inside(w, source)]
            if len(editors) != 1:
                raise RuntimeError(f'{key} needs a uniform scalar source')
            editors[0].setText(format(value, '.9g'))
            editors[0].editingFinished.emit()
            _frame(qt)
            displayed = float(editors[0].text())
            result[key] = {'requested_scalar': value, 'display_value': displayed}
            if abs(displayed-value) > 0.00051:
                raise RuntimeError(f'{key} readback differs from the requested value')
    for key, enabled in toggles.items():
        if buttons[key].isChecked() != enabled:
            buttons[key].click()
            _frame(qt)
        if buttons[key].isChecked() != enabled:
            raise RuntimeError(f'Painter did not change the {key} channel toggle')
    return {'values': result, 'enabled': {k: b.isChecked() for k, b in buttons.items()}}


def _frame(qt):
    core = qt[0]
    loop = core.QEventLoop()
    core.QTimer.singleShot(40, loop.quit)
    loop.exec(core.QEventLoop.ExcludeUserInputEvents)


class Point(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double)]


class BlockDescriptor(C.Structure):
    _fields_ = [('reserved', C.c_ulong), ('size', C.c_ulong), ('signature', C.c_char_p)]


class EventBlock(C.Structure):
    _fields_ = [('isa', C.c_void_p), ('flags', C.c_int), ('reserved', C.c_int),
                ('invoke', C.c_void_p), ('descriptor', C.POINTER(BlockDescriptor))]


class AppKit:
    def __init__(self, window):
        self.lib = C.CDLL('/usr/lib/libobjc.A.dylib')
        self.lib.objc_getClass.argtypes = [C.c_char_p]
        self.lib.objc_getClass.restype = C.c_void_p
        self.lib.sel_registerName.argtypes = [C.c_char_p]
        self.lib.sel_registerName.restype = C.c_void_p
        self.address = C.cast(self.lib.objc_msgSend, C.c_void_p).value
        self.app = self.send(self.lib.objc_getClass(b'NSApplication'), 'sharedApplication')
        self.view = int(window.winId())
        if self.view <= 2**32:
            raise RuntimeError('Painter did not expose a native macOS window view')
        self.window = self.send(self.view, 'window')
        if not self.window:
            raise RuntimeError('Painter native window is unavailable')
        self.number = self.send(self.window, 'windowNumber', C.c_long)
        self.flipped = self.send(self.view, 'isFlipped', C.c_bool)
        self.count = 0

    def send(self, obj, name, result=C.c_void_p, args=(), values=()):
        method = C.CFUNCTYPE(result, C.c_void_p, C.c_void_p, *args)(self.address)
        return method(obj, self.lib.sel_registerName(name.encode()), *values)

    def activate(self):
        self.send(self.window, 'makeKeyAndOrderFront:', None, (C.c_void_p,), (None,))
        self.send(self.app, 'activateIgnoringOtherApps:', None, (C.c_bool,), (True,))

    def active(self):
        return (self.send(self.app, 'isActive', C.c_bool)
                and self.send(self.app, 'keyWindow') == self.window)

    def watch_input(self, state):
        import atexit

        kinds = (1, 2, 3, 4, 6, 7, 10, 11, 18, 19, 20, 22, 23, 25, 26, 27, 29, 30, 31, 34)

        @C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)
        def handle(block, event):
            try:
                state['input_serial'] += 1
            except BaseException as exc:
                state['monitor_error'] = str(exc)
            return event

        runtime = C.CDLL('/usr/lib/libSystem.B.dylib')
        isa = C.addressof((C.c_void_p * 32).in_dll(runtime, '_NSConcreteGlobalBlock'))
        descriptor = BlockDescriptor(0, C.sizeof(EventBlock), b'@@?@')
        block = EventBlock(isa, (1 << 28) | (1 << 30), 0,
                           C.cast(handle, C.c_void_p), C.pointer(descriptor))
        monitor = self.send(self.lib.objc_getClass(b'NSEvent'),
                            'addLocalMonitorForEventsMatchingMask:handler:', C.c_void_p,
                            (C.c_ulonglong, C.c_void_p),
                            (sum(1 << kind for kind in kinds), C.addressof(block)))
        if not monitor:
            raise RuntimeError('Painter native input monitor could not be installed')
        # NSApplication retains the global block pointer; keep all Python-backed
        # storage and the callable alive for the application lifetime too.
        state['native_monitor'] = (monitor, block, descriptor, handle, runtime, self)

        def stop():
            self.send(self.lib.objc_getClass(b'NSEvent'), 'removeMonitor:', None,
                      (C.c_void_p,), (monitor,))

        atexit.register(stop)

    def event(self, kind, position, pressure):
        self.count += 1
        event = self.send(
            self.lib.objc_getClass(b'NSEvent'),
            'mouseEventWithType:location:modifierFlags:timestamp:windowNumber:context:'
            'eventNumber:clickCount:pressure:', C.c_void_p,
            (C.c_ulong, Point, C.c_ulong, C.c_double, C.c_long, C.c_void_p,
             C.c_long, C.c_long, C.c_float),
            (kind, position, 0, time.monotonic(), self.number, None, self.count, 1,
             0.0 if kind == 2 else pressure))
        if not event:
            raise RuntimeError('AppKit did not create a brush event')
        self.send(self.app, 'sendEvent:', None, (C.c_void_p,), (event,))


def _state(module, qt, native):
    state = getattr(module, STATE_ATTR, None)
    if state is None:
        old = getattr(module, '_cavi_mcp_brush_v1', None)
        if old and old.get('filter') is not None:
            qt[2].QApplication.instance().removeEventFilter(old['filter'])
        state = {'context': None, 'input_serial': 0}
        # Match native input events without an application-wide Python Qt filter,
        # which makes Shiboken wrap transient internal QObjects recursively.
        native.watch_input(state)
        setattr(module, STATE_ATTR, state)
    return state


def invalidate(module):
    state = getattr(module, STATE_ATTR, None)
    if state is not None:
        state['context'] = None


def take_context(state, params, clock=time.monotonic):
    context = state['context']
    if (context is None or not isinstance(params.get('context_token'), str)
            or not secrets.compare_digest(context['token'], params['context_token'])
            or context['lease_id'] != params.get('lease_id')):
        raise RuntimeError('Brush context is missing or stale; prepare again')
    state['context'] = None
    if clock() >= context['expires'] or state['input_serial'] != context['input_serial']:
        raise RuntimeError('Painter input changed or brush context expired; prepare again')
    return context


def _channel_combo(qt):
    choices = []
    for widget in qt[2].QApplication.allWidgets():
        try:
            if (qt[3].isValid(widget) and isinstance(widget, qt[2].QComboBox)
                    and widget.isVisible()
                    and widget.parent().objectName() == 'viewermode_viewport_combobox'):
                choices.append(widget)
        except RuntimeError:
            continue
    if len(choices) != 1:
        raise RuntimeError('Painter channel selector is unavailable')
    return choices[0]


def _fingerprint(window, viewer, combo, stack, layerstack, qt):
    position = viewer.mapToGlobal(qt[0].QPoint(0, 0))
    return {
        'window': int(window.winId()), 'viewer': int(viewer.winId()),
        'geometry': [position.x(), position.y(), viewer.width(), viewer.height()],
        'dpr': float(viewer.devicePixelRatioF()), 'channel': combo.currentText(),
        'selected': [node.uid() for node in layerstack.get_selected_nodes(stack)],
        'selection_type': [layerstack.get_selection_type(node).name
                           for node in layerstack.get_selected_nodes(stack)],
        'formats': {c.name: v.format().name for c, v in stack.all_channels().items()},
    }


def run(params, directory, module, layerstack, textureset, project, ui, require_lease):
    action = params.get('action')
    if action not in {'prepare', 'stroke', 'settings'}:
        raise ValueError('Unknown brush action')
    path = points(params.get('points')) if action == 'stroke' else None
    qt = _qt()
    core, gui, widgets, _ = qt
    app = widgets.QApplication
    if app.activeModalWidget() is not None or app.mouseButtons() != core.Qt.NoButton:
        raise RuntimeError('Close the modal dialog or release mouse buttons before brush input')
    window = ui.get_main_window()
    native = AppKit(window)
    state = _state(module, qt, native)
    if state.get('monitor_error'):
        raise RuntimeError('Painter native input monitor failed; restart the application')
    if action == 'settings':
        state['context'] = None
        require_lease()
        preset = apply_preset(qt, params['preset']) if params.get('preset') else None
        channels = channel_controls(qt, window, native, params.get('channel_values'),
                                    params.get('enabled_channels'))
        return {'settings': settings(qt, params.get('settings')), 'preset': preset,
                'channels': channels}
    if action == 'prepare':
        state['context'] = None
        stack = textureset.Stack.from_name(params['texture_set'])
        node = layerstack.get_node_by_uid(params['uid'])
        target = params.get('target', 'content')
        if (node.get_texture_set().name() != params['texture_set'] or not node.is_visible()
                or target == 'content' and not isinstance(node, layerstack.PaintLayerNode)):
            raise ValueError('Content painting needs a visible PaintLayerNode; masks need a layer')
        if target == 'mask' and not node.has_mask():
            raise ValueError('The brush target has no paint mask')
        native.activate()
        textureset.set_active_stack(stack)
        layerstack.set_selected_nodes([node])
        layerstack.set_selection_type(node, layerstack.SelectionType.Mask if target == 'mask'
                                     else layerstack.SelectionType.Content)
        mode = params.get('mode', 'paint')
        for key in (core.Qt.Key_F3,):
            for kind in (core.QEvent.KeyPress, core.QEvent.KeyRelease):
                app.sendEvent(window, gui.QKeyEvent(kind, key, core.Qt.NoModifier))
        app.processEvents()
        preset = apply_preset(qt, params['preset']) if params.get('preset') else None
        tool = _widgets(qt, 'Eraser' if mode == 'erase' else 'Paint')
        action = tool.defaultAction()
        if action is None:
            raise RuntimeError('Painter tool action is unavailable')
        action.trigger()
        _frame(qt)
        if not _widgets(qt, 'Eraser' if mode == 'erase' else 'Paint').isChecked():
            raise RuntimeError('Painter did not select the requested paint/erase tool')
        brush_settings = settings(qt, params.get('settings'))
        channels = channel_controls(qt, window, native, params.get('channel_values'),
                                    params.get('enabled_channels'))
        combo = _channel_combo(qt)
        index = combo.findText('Base color', core.Qt.MatchFixedString)
        if index < 0:
            raise RuntimeError('The Texture Set needs a Base color channel for this brush view')
        combo.setCurrentIndex(index)
        combo.activated.emit(index)
        _frame(qt)
        combo = _channel_combo(qt)  # Painter can rebuild the selector after activation.
        viewer = _widgets(qt, 'TextureViewer')
        if not native.active():
            raise RuntimeError('Painter could not activate its native window')
        snapshot = _fingerprint(window, viewer, combo, stack, layerstack, qt)
        if snapshot['selected'] != [params['uid']] or snapshot['channel'] != 'Base color':
            raise RuntimeError('Painter did not select the requested brush target')
        # Capture the native window: QWidget.grab omits Painter's GPU viewport.
        pixmap = window.screen().grabWindow(window.winId())
        if pixmap.isNull() or not pixmap.save(str(directory / 'painter.png')):
            raise RuntimeError('Painter native window capture failed')
        offset = viewer.mapTo(window, core.QPoint(0, 0))
        dpr = pixmap.devicePixelRatioF()
        crop = pixmap.copy(int(offset.x()*dpr), int(offset.y()*dpr),
                           int(viewer.width()*dpr), int(viewer.height()*dpr))
        if not crop.save(str(directory / 'viewport.png')):
            raise RuntimeError('Painter viewport capture failed')
        token = secrets.token_urlsafe(24)
        state['context'] = {
            'token': token, 'expires': time.monotonic() + 60, 'snapshot': snapshot,
            'project': str(project.get_uuid()), 'file': project.file_path(),
            'texture_set': params['texture_set'], 'uid': params['uid'],
            'lease_id': params.get('lease_id'), 'input_serial': state['input_serial'],
            'mode': mode, 'target': target,
        }
        return {'prepared': True, 'context_token': token, 'expires_in': 60,
                'texture_set': params['texture_set'], 'uid': params['uid'],
                'viewport': snapshot, 'preview': str(directory / 'viewport.png'),
                'window_preview': str(directory / 'painter.png'),
                'brush_settings': brush_settings, 'mode': mode, 'target': target,
                'preset': preset, 'channels': channels}

    context = take_context(state, params)
    stack = textureset.Stack.from_name(context['texture_set'])
    viewer, combo = _widgets(qt, 'TextureViewer'), _channel_combo(qt)

    def guard():
        require_lease()
        if (str(project.get_uuid()) != context['project'] or project.file_path() != context['file']
                or project.is_busy() or not native.active() or app.activeModalWidget() is not None
                or _fingerprint(window, viewer, combo, stack, layerstack, qt)
                != context['snapshot']):
            raise RuntimeError('Painter project, target, viewport or focus changed; prepare again')

    def send(kind, point):
        position = viewer.mapTo(window, core.QPoint(int(viewer.width()*point[0]),
                                                   int(viewer.height()*point[1])))
        local = Point(position.x(), position.y() if native.flipped
                      else window.height()-position.y())
        native_position = native.send(native.view, 'convertPoint:toView:', Point,
                                      (Point, C.c_void_p), (local, None))
        native.event(kind, native_position, point[2])
        _frame(qt)

    gesture(path, send, guard)
    return {'dispatched': True, 'points': len(path), 'texture_set': context['texture_set'],
            'uid': context['uid'], 'needs_saving': project.needs_saving(),
            'next': 'Inspect or export the painted channels before accepting the stroke; '
                    'prepare again for another stroke.'}
