"""SAM-compatible tool wrappers for VerdictCouncil.

These tools implement the DynamicTool interface from solace-agent-mesh
so they can be registered with the SAM orchestrator for distributed
agent-to-tool RPC calls.
"""

from src.tools.sam.search_precedents_tool import SearchPrecedentsTool

__all__ = ["SearchPrecedentsTool"]
