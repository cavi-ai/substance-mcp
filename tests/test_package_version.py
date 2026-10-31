import asyncio
import sys
import unittest
from importlib.metadata import version

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVERS = {
    "cavi_substance_mcp.server": "cavi-substance-mcp",
    "cavi_substance_mcp.designer.server": "cavi-substance-designer-mcp",
    "cavi_substance_mcp.sampler.server": "cavi-substance-sampler-mcp",
}


async def server_info(module):
    params = StdioServerParameters(command=sys.executable, args=["-m", module])
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=60) as session,
    ):
        return (await session.initialize()).server_info


class ServerVersionTests(unittest.TestCase):
    def test_server_info_reports_the_package_version_not_the_sdk_version(self):
        package = version("cavi-substance-mcp")
        for module, name in SERVERS.items():
            with self.subTest(server=name):
                info = asyncio.run(server_info(module))
                self.assertEqual(info.name, name)
                self.assertEqual(info.version, package)


if __name__ == "__main__":
    unittest.main()
