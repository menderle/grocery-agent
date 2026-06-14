"""Self-hosted local web UI for the grocery agent.

A second front door alongside the Claude/MCP connector: a Starlette app whose brain is a
Claude tool-use agent loop driving the SAME grocery-gateway in-process. It shares all
state (policy, approvals, audit, preferences, the HEB cart) with the connector path —
nothing here can bypass the money-safety guards, which live in place_order.
"""
