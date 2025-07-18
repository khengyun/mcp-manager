# FastMCP Swagger Server

This example demonstrates how to expose one or more OpenAPI specifications through the [FastMCP](https://pypi.org/project/fastmcp/) server. Each Swagger specification is loaded from a local path or URL and its endpoints are registered as MCP tools.

## Requirements

- Python 3.12
- `fastmcp` package (`pip install fastmcp`)
- `fastapi` package (`pip install fastapi`)

## Usage

```bash
pip install -r requirements.txt  # install dependencies
python server.py [config.json or URL]
```

### Running tests

```bash
pytest
```

Alternatively set the `CONFIG_URL` environment variable to a file path or URL
before running the server.

Multiple config files can be provided by separating paths with commas or by
setting the `EXTRA_CONFIGS` environment variable. Set `DB_URL` to a PostgreSQL
connection string to store or retrieve the loaded configuration. The current
configuration can be written to disk by setting the `EXPORT_CONFIG` environment
variable to a file path.

### Docker Compose

The repository includes a `docker-compose.yml` that starts the server
alongside a PostgreSQL instance. Build and run the services with:

```bash
docker compose up --build
```

The default configuration is loaded from `fastmcp_server/config.json` and
stored in the `db` service. The server is available on `http://localhost:3000`.

By default the server listens on port `3000`. Each Swagger specification becomes its own MCP server mounted under its configured `prefix`. SSE connections for a spec are available at `/<prefix>/sse` with messages posted to `/<prefix>/messages`. A combined server exposing all tools is also mounted at `/sse` and `/messages`. A simple health check is available at `/health`, the list of prefixes can be retrieved from `/list-server`, and the tools of a server can be listed via `/list-tools` (use the optional `prefix` query parameter to limit the results).

When the server starts it prints a short summary of how many tools were loaded for each Swagger specification and the total number of tools across all specs:

```
Loaded N Swagger servers:
  - prefix1: X tools
  - prefix2: Y tools
Total tools available: Z
```

The OpenAPI schemas to load are configured in `config.json`. Multiple specifications can be provided using the `swagger` array. Each entry must include a `path` pointing to either a local file or a remote URL, an `apiBaseUrl` and a unique `prefix` used for the mount paths.

Example `config.json`:

```json
{
  "swagger": [
    {
      "path": "examples/swagger-pet-store.json",
      "apiBaseUrl": "https://petstore.swagger.io/v2",
      "prefix": "petstore"
    },
    {
      "path": "https://example.com/other-openapi.json",
      "apiBaseUrl": "https://example.com/api",
      "prefix": "remote"
    }
  ],
  "server": {
    "host": "0.0.0.0",
    "port": 3000
  },
  "database": "postgresql+asyncpg://user:pass@host/dbname"
}
```

Additional Swagger specifications can be added to the `swagger` list with different prefixes to combine multiple APIs into one MCP server. For example, a prefix of `petstore` will expose endpoints at `/petstore/sse` and `/petstore/messages`.

### Adding specs at runtime

Running servers can register new Swagger specifications by POSTing a JSON
payload to the `/add-server` endpoint. The body should contain the same
fields used in `config.json` (`path`, `apiBaseUrl` and optional `prefix`).
The new API will immediately be mounted under its prefix and listed by
`/list-server`. Tools for a specific API can be retrieved from `/list-tools?prefix=<prefix>`.

### Exporting Swagger specs

The raw OpenAPI schema for any loaded server can be downloaded via
`/export-server/{prefix}`. If the prefix is unknown a `404` response is
returned.

### Database persistence

Set the `database` field in `config.json` (or the `DB_URL` environment
variable) to a SQLAlchemy connection URL for a PostgreSQL database.
Registered Swagger specs are stored in this database when added via
`/add-server` and reloaded automatically on startup so no information is
lost across restarts. The server will retry connecting to the database for a
short time on startup which helps when running with Docker where the database
service might need a few seconds to become available.

### Disabling tools

Individual tools can be enabled or disabled at runtime via the `/tool-enabled`
endpoint. Send a POST request with a JSON body containing the tool `name`, the
server `prefix` and the desired `enabled` state:

```bash
curl -X POST http://localhost:3000/tool-enabled \
  -H "Content-Type: application/json" \
  -d '{"prefix": "petstore", "name": "findPetsByStatus", "enabled": false}'
```

The state is persisted in the database so disabled tools remain disabled across
server restarts.

Tools can also be disabled programmatically using the FastMCP API:

```python
from fastmcp import FastMCP

server = FastMCP(name="example")

@server.tool
def dynamic():
    return "hi"

dynamic.disable()  # re-enable later with dynamic.enable()
```
