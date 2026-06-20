# Marketing pack

Reusable copy for launching `tableau-mcp-publish`.

## Loom script (70–90s)

> **Hook (5s):** "Tableau's official MCP server lets an agent read your data. It can't publish
> anything. So I built the half that can."

1. **(5–18s)** Show the MCP client config with **both** servers — `tableau` (official) and
   `tableau-publish` (this) — sharing one PAT. *Caption: "Runs alongside the official server. Same
   credentials."*
2. **(18–38s)** In Claude/Cursor, type: *"Take the top 100 customers by revenue from this Snowflake
   query and publish it as a governed datasource called Top Customers in the Sales project."* Watch
   the agent call `create_datasource_from_query`. Cut to logs: query runs → `.hyper` built →
   `.tdsx` packaged → published. The agent returns a Cloud URL.
3. **(38–52s)** Click the URL → the published datasource opens in Tableau Cloud, in the Sales
   project, with the data. *Caption: "SQL to governed published datasource. One tool call."*
4. **(52–70s)** Type: *"Now build a starter workbook with a bar chart of revenue by region on that
   datasource."* Agent calls `create_starter_workbook` → returns a workbook URL. Open it → a real
   Tableau workbook with the bar chart, live on Cloud.
5. **(70–82s)** Show `docs/tool_reference.md` — the full tool list. *Caption: "Datasources,
   workbooks, projects, permissions, refresh. The write side of Tableau MCP."*

> **CTA (82–90s):** "Repo at github.com/SebAustin/tableau-mcp-publish. It's the authoring companion
> to Tableau's official MCP server — and I've opened a discussion to propose it upstream. Tableau
> Ambassador, available for analytics + AI engineering."

## LinkedIn post

Tableau shipped an official MCP server this year. It's genuinely good — an AI agent can query your
published datasources, read column metadata, pull Pulse metrics. It's also, by design, read-only.
It cannot create a datasource, author a workbook, or publish anything to Cloud.

As a Tableau Ambassador, publishing is the half I live in. So I built the companion.

**tableau-mcp-publish** is an MCP server for the write side of Tableau. It runs alongside the
official server, shares the same Personal Access Token, and adds the tools the official one
doesn't have: create a published datasource from a SQL query or a DataFrame, generate a starter
workbook, and publish both to Tableau Cloud — all callable by an agent.

The flagship tool is `create_datasource_from_query`. You hand it a connection, a SQL string, a
datasource name, and a project. It runs the query, materializes a `.hyper` extract, packages a
`.tdsx`, and publishes it to your Cloud site — returning the URL. One tool call, fully
natural-language drivable:

> "Take the top 100 customers by revenue from Snowflake and publish it as a governed datasource
> called Top Customers in the Sales project, then build a starter workbook with a bar chart of
> revenue by region."

Two tool calls later, there's a governed datasource and a workbook live on Cloud.

The architecture is two layers: a TypeScript MCP server that matches the official server's
conventions (so it drops into the same client config), and a Python sidecar that does the Hyper API
and document authoring — because those are Python-first and there's no reason to reimplement them.
The TypeScript layer handles REST auth and the chunked publish path for large extracts.

I've opened a discussion on the official repo proposing these tools upstream. Whether they land
there or live as a companion package, the point stands: an agent should be able to go from "query
this data" all the way to "publish a governed datasource and a workbook" without leaving the
conversation.

github.com/SebAustin/tableau-mcp-publish

#Tableau #MCP #ModelContextProtocol #DataEngineering #AI #BusinessIntelligence
