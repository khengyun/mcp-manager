"""Route handlers and shared state for the Swagger server."""

from fastapi import FastAPI, Request, HTTPException
import models
from fastmcp import FastMCP
from fastmcp.server.openapi import FastMCPOpenAPI
import httpx

import db
from utils.openapi_utils import _get_prefix, _load_spec

# Runtime storage for loaded OpenAPI specs and their configs
spec_data: dict[str, dict] = {}
spec_configs: dict[str, dict] = {}
search_status: dict[str, bool] = {}


HealthResponse = models.HealthResponse


ListServersResponse = models.ListServersResponse


ListToolsResponse = models.ListToolsResponse


AddServerRequest = models.AddServerRequest


AddServerResponse = models.AddServerResponse


ToolEnabledRequest = models.ToolEnabledRequest


ToolEnabledResponse = models.ToolEnabledResponse


async def close_clients(
    clients: list[httpx.AsyncClient], session_maker: db.async_sessionmaker
) -> None:
    for client in clients:
        await client.aclose()
    await session_maker.bind.dispose()


async def health() -> HealthResponse:
    """Simple health check."""
    return HealthResponse(status="ok")


def make_list_servers_handler(server_info: list[tuple[str, int]]):
    async def list_servers(_: Request) -> ListServersResponse:
        """Return list of loaded Swagger server prefixes."""
        return ListServersResponse(servers=[p for p, _ in server_info])

    return list_servers


def make_list_tools_handler(root_server: FastMCP):
    """Return a handler that lists available tools.

    If a ``prefix`` query parameter is provided only tools for that
    mounted server are returned. Otherwise all tools registered on the
    root server are listed.
    """

    async def list_tools(request: Request) -> ListToolsResponse:
        """List tools for the given prefix or all servers."""
        prefix = request.query_params.get("prefix")
        server = root_server if prefix is None else root_server._mounted_servers.get(prefix)
        if server is None:
            raise HTTPException(status_code=404, detail="prefix not found")

        tools = await server.get_tools()
        return ListToolsResponse(tools=list(tools))

    return list_tools


def make_add_server_handler(
    root_server: FastMCP,
    app: FastAPI,
    server_info: list[tuple[str, int]],
    clients: list[httpx.AsyncClient],
    cfg: dict,
    session_maker: db.async_sessionmaker,
):
    async def add_server(spec: AddServerRequest) -> AddServerResponse:
        """Dynamically mount a new Swagger specification."""
        spec_cfg = spec.model_dump()
        prefix = _get_prefix(spec_cfg)

        if any(p == prefix for p, _ in server_info):
            raise HTTPException(status_code=400, detail="prefix already exists")

        try:
            loaded_spec = _load_spec(spec_cfg)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        client = httpx.AsyncClient(base_url=spec_cfg["apiBaseUrl"])
        sub_server = FastMCPOpenAPI(
            openapi_spec=loaded_spec,
            client=client,
            name=f"{spec_cfg.get('prefix', 'api')} server",
        )
        spec_data[prefix] = spec.model_dump()
        spec_configs[prefix] = spec_cfg

        tool_count = len(await sub_server.get_tools())

        root_server.mount(prefix, sub_server)
        app.mount(f"/{prefix}", sub_server.sse_app())

        server_info.append((prefix, tool_count))
        clients.append(client)
        cfg.setdefault("swagger", []).append(spec_cfg)

        async with session_maker() as session:
            await db.add_spec(session, spec_cfg)

        return AddServerResponse(added=prefix, tools=tool_count)

    return add_server


async def export_server(prefix: str, _: Request) -> dict:
    """Return the stored OpenAPI specification for a prefix."""
    if prefix not in spec_data:
        raise HTTPException(status_code=404, detail="prefix not found")
    return spec_data[prefix]


def make_set_tool_enabled_handler(
    root_server: FastMCP, session_maker: db.async_sessionmaker
):
    async def set_tool_enabled(data: ToolEnabledRequest) -> ToolEnabledResponse:
        """Enable or disable a specific tool by prefix and name."""
        prefix = data.prefix
        name = data.name
        enabled = data.enabled
        if not prefix or not name:
            raise HTTPException(status_code=400, detail="prefix and name required")
        server = root_server._mounted_servers.get(prefix)
        if server is None:
            raise HTTPException(status_code=404, detail="prefix not found")
        tools = await server.get_tools()
        if name not in tools:
            raise HTTPException(status_code=404, detail="tool not found")
        tool = tools[name]
        if enabled:
            tool.enable()
        else:
            tool.disable()
        async with session_maker() as session:
            await db.set_tool_enabled(session, prefix, name, bool(enabled))
        return ToolEnabledResponse(tool=name, enabled=bool(enabled))

    return set_tool_enabled


def make_set_search_enabled_handler():
    async def set_search_enabled(data: models.SearchEnabledRequest) -> models.SearchEnabledResponse:
        """Enable or disable search for a server."""
        prefix = data.prefix
        if prefix not in spec_data:
            raise HTTPException(status_code=404, detail="prefix not found")
        search_status[prefix] = bool(data.enabled)
        return models.SearchEnabledResponse(prefix=prefix, enabled=bool(data.enabled))

    return set_search_enabled


def make_search_handler(root_server: FastMCP):
    async def search(request: Request) -> models.SearchResponse:
        """Search tools with optional filters."""
        prefix = request.query_params.get("prefix")
        name_filter = request.query_params.get("name")
        enabled_param = request.query_params.get("enabled")
        enabled_filter: bool | None = None
        if enabled_param is not None:
            enabled_filter = enabled_param.lower() in {"1", "true", "yes"}

        prefixes = [prefix] if prefix else list(root_server._mounted_servers.keys())
        results: list[models.SearchResult] = []
        for pre in prefixes:
            server = root_server._mounted_servers.get(pre)
            if server is None:
                raise HTTPException(status_code=404, detail="prefix not found")
            if enabled_filter is not None and search_status.get(pre, True) != enabled_filter:
                continue
            tools = await server.get_tools()
            for tool_name in tools:
                if name_filter and name_filter.lower() not in tool_name.lower():
                    continue
                results.append(models.SearchResult(prefix=pre, tool=tool_name))

        return models.SearchResponse(results=results)

    return search

