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

STATE_ATTR = '_cavi_mcp_brush_v1'


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


def _frame(qt):
    core = qt[0]
    loop = core.QEventLoop()
    core.QTimer.singleShot(40, loop.quit)
    loop.exec(core.QEventLoop.ExcludeUserInputEvents)


class Point(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double)]


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


def _state(module, qt):
    state = getattr(module, STATE_ATTR, None)
    if state is None:
        state = {'context': None, 'input_serial': 0}
        core, _, widgets, _ = qt

        class InputChanges(core.QObject):
            def eventFilter(self, obj, event):
                if event.type() in (core.QEvent.MouseButtonPress, core.QEvent.MouseMove,
                                    core.QEvent.Wheel, core.QEvent.KeyPress,
                                    core.QEvent.TabletPress, core.QEvent.TabletMove):
                    state['input_serial'] += 1
                return False

        state['filter'] = InputChanges(widgets.QApplication.instance())
        widgets.QApplication.instance().installEventFilter(state['filter'])
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
    if action not in {'prepare', 'stroke'}:
        raise ValueError('Unknown brush action')
    path = points(params.get('points')) if action == 'stroke' else None
    qt = _qt()
    core, gui, widgets, _ = qt
    app = widgets.QApplication
    if app.activeModalWidget() is not None or app.mouseButtons() != core.Qt.NoButton:
        raise RuntimeError('Close the modal dialog or release mouse buttons before brush input')
    state = _state(module, qt)
    window = ui.get_main_window()
    native = AppKit(window)
    if action == 'prepare':
        state['context'] = None
        stack = textureset.Stack.from_name(params['texture_set'])
        node = layerstack.get_node_by_uid(params['uid'])
        if (node.get_texture_set().name() != params['texture_set']
                or not isinstance(node, layerstack.PaintLayerNode) or not node.is_visible()):
            raise ValueError('Brush target must be a visible PaintLayerNode in the Texture Set')
        native.activate()
        textureset.set_active_stack(stack)
        layerstack.set_selected_nodes([node])
        layerstack.set_selection_type(node, layerstack.SelectionType.Content)
        for key in (core.Qt.Key_F3, core.Qt.Key_B):
            for kind in (core.QEvent.KeyPress, core.QEvent.KeyRelease):
                app.sendEvent(window, gui.QKeyEvent(kind, key, core.Qt.NoModifier))
        app.processEvents()
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
        }
        return {'prepared': True, 'context_token': token, 'expires_in': 60,
                'texture_set': params['texture_set'], 'uid': params['uid'],
                'viewport': snapshot, 'preview': str(directory / 'viewport.png'),
                'window_preview': str(directory / 'painter.png'),
                'brush_settings': 'Current Painter brush and enabled material channels'}

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
