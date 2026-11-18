"""Run one native Designer graph transaction, preserving its input packages."""

import json
import time
from pathlib import Path

from cavi_mcp_core import apps, proc

from .. import receipts
from ..core import host_script, workspace
from ..core.lock import FileLock
from . import runner
from .graph_models import GraphRecipe

IN_APP = "cavi_substance_mcp.designer.graph_native"
LOCK_NAME = ".cavi-designer-authoring.lock"


def executable():
    directory = runner.installation().bin_dir
    for name in ('Adobe Substance 3D Designer', 'Adobe Substance 3D Designer.exe',
                 'Adobe Substance 3D Designer.cmd'):
        path = directory / name
        if path.is_file():
            return path.resolve()
    raise RuntimeError('Designer native application not found beside its CLIs')


class AppLock(FileLock):
    """Fail before launch if another cooperating Designer authoring request owns the app."""

    def __init__(self, root):
        super().__init__(Path(root) / LOCK_NAME)

    def __enter__(self):
        if not self.try_lock():
            raise RuntimeError('Another Designer authoring request is active; nothing launched')
        return self

    def __exit__(self, *_):
        self.release()


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
    source_hash = receipts.sha256_file(source) if source else None
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
            host_script.prepare(script, request, response, IN_APP, {
                'action': action, 'recipe': data, 'path': str(source) if source else None,
                'output': str(output), 'libraries': dependencies, 'query': query,
                'offset': offset, 'limit': limit,
            })
            result = await proc.run([app, '--startup-script', script, '--quit'], timeout_seconds)
            if result.returncode != 0:
                raise RuntimeError(runner.failure(result, 'Designer graph authoring'))
            if not response.is_file():
                raise RuntimeError('Designer did not produce a complete graph response')
            reply = host_script.envelope(response, 'Designer')
            if not reply['ok']:
                host_script.raise_error(str(reply.get('error')))
            if source and receipts.sha256_file(source) != source_hash:
                raise RuntimeError('Source package changed during authoring; inspect the source')
            value = reply.get('result')
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
