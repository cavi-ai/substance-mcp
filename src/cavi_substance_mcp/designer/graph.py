"""Run one native Designer graph transaction, preserving its input packages."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from cavi_mcp_core import apps, proc

from .. import receipts
from ..core import workspace
from . import runner
from .graph_models import GraphRecipe

DRIVER = '''import json,sys
from pathlib import Path
sys.path.insert(0, {package_root!r})
from cavi_substance_mcp.designer.graph_native import run
request=Path({request!r})
response=Path({response!r})
try:
    result={{"ok":True,"result":run(json.loads(request.read_text(encoding="utf-8")))}}
except BaseException as exc:
    result={{"ok":False,"error":type(exc).__name__+": "+str(exc)}}
response.write_text(json.dumps(result,allow_nan=False),encoding="utf-8")
'''


def executable():
    directory = runner.installation().bin_dir
    for name in ('Adobe Substance 3D Designer', 'Adobe Substance 3D Designer.exe',
                 'Adobe Substance 3D Designer.cmd'):
        path = directory / name
        if path.is_file():
            return path.resolve()
    raise RuntimeError('Designer native application not found beside its CLIs')


class AppLock:
    """Fail before launch if another cooperating Designer authoring request owns the app."""

    def __init__(self, root):
        self.path, self.fd = root / '.cavi-designer-authoring.lock', None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if sys.platform == 'win32':
                import msvcrt
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b'\0')
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            self.fd = None
            raise RuntimeError('Another Designer authoring request is active; '
                               'nothing launched') from exc
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def recipe_data(recipe):
    value = recipe.model_dump(mode='json')
    # Creation has high-precision defaults; edits preserve undeclared source settings.
    for name in ('identifier', 'resolution', 'precision', 'seed'):
        if name not in recipe.model_fields_set:
            value.pop(name)
    return value


def package_source(name, root):
    if name.startswith('builtin:'):
        resources = runner.installation().resources
        if resources is None:
            raise RuntimeError('Designer built-in resources were not found')
        suffix = name.removeprefix('builtin:')
        path = (resources / suffix).with_suffix('.sbs').resolve()
        if (not path.is_relative_to(resources) or not path.is_file()
                or path.parent.name not in ('packages', 'templates')):
            raise ValueError('Built-in package must name an installed packages/ or templates/ SBS')
        return path
    return workspace.input_file(root, name, {'.sbs'})


def libraries(operations, root):
    result = {}
    for operation in operations:
        if operation['action'] != 'instance':
            continue
        name = operation['package']
        path = package_source(name, root)
        result[name] = str(path)
    return result


async def invoke(action, directory, path=None, recipe=None, query='', offset=0, limit=100,
                 timeout_seconds=300):
    root = workspace.root(create=True)
    if action not in ('create', 'edit', 'inspect', 'catalog'):
        raise ValueError('Unknown graph action')
    if action in ('edit', 'inspect') and path is None:
        raise ValueError(f'{action} needs path')
    if action == 'create' and path is not None:
        raise ValueError('create does not accept a source path')
    recipe = GraphRecipe.model_validate(recipe or {})
    data = recipe_data(recipe)
    source = (package_source(path, root) if action == 'catalog' and path else
              workspace.input_file(root, path, {'.sbs'}) if path else None)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest() if source else None
    dependencies = libraries(data['operations'], root)
    app = executable()
    started = time.monotonic()
    with AppLock(root):
        running = apps.running(app)
        if running is not False:
            raise RuntimeError('Designer is running or its state is unknown; nothing launched. '
                               'Save and close that instance before batch authoring.')
        with workspace.new_output_directory(root, directory) as folder:
            output = folder / 'material.sbs'
            request = folder / '.graph-request.json'
            response = folder / '.graph-response.json'
            script = folder / '.graph-driver.py'
            request.write_text(json.dumps({
                'action': action, 'recipe': data, 'path': str(source) if source else None,
                'output': str(output), 'libraries': dependencies, 'query': query,
                'offset': offset, 'limit': limit,
            }, allow_nan=False), encoding='utf-8')
            script.write_text(DRIVER.format(package_root=str(Path(__file__).parents[2]),
                                            request=str(request), response=str(response)),
                              encoding='utf-8')
            result = await proc.run([app, '--startup-script', script, '--quit'], timeout_seconds)
            if result.returncode != 0:
                raise RuntimeError(runner.failure(result, 'Designer graph authoring'))
            if not response.is_file() or response.stat().st_size > 8 * 1024**2:
                raise RuntimeError('Designer did not produce a complete graph response')
            reply = json.loads(response.read_text(encoding='utf-8'))
            if not reply.get('ok'):
                raise RuntimeError('Designer graph authoring failed: ' + str(reply.get('error')))
            if source and hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
                raise RuntimeError('Source package changed during authoring; inspect the source')
            value = reply['result']
            report = folder / 'graph.json'
            report.write_text(json.dumps(value, allow_nan=False, indent=2), encoding='utf-8')
            files = [report]
            if action in ('create', 'edit'):
                if not output.is_file() or output.stat().st_size == 0:
                    raise RuntimeError('Designer did not save the authored SBS package')
                files.append(output)
            all_sources = {**dependencies, **({path: str(source)} if source else {})}
            manifest = receipts.write_manifest(
                folder, 'designer', 'graph_' + action, root,
                inputs=[Path(p) for p in all_sources.values() if Path(p).is_relative_to(root)],
                external=[receipts.app_resource(name, Path(p))
                          for name, p in all_sources.items() if not Path(p).is_relative_to(root)],
                outputs=files, parameters={'recipe': data}, seconds=time.monotonic()-started)
            for private in (request, response, script):
                private.unlink()
            return {**value, 'action': action, 'directory': workspace.rel(root, folder),
                    'package': workspace.rel(root, output) if output in files else None,
                    'manifest': workspace.rel(root, manifest),
                    'report': workspace.rel(root, report)}
