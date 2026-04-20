# Lessons Learned

- When diagrams need to be visible on GitHub, do not rely on raw `.mmd` or `.puml` files alone. Add a Markdown page with an embedded Mermaid block and render PlantUML to an image artifact such as SVG.
- In Mermaid flowcharts, wrap node labels in quotes when they contain parentheses or other punctuation-heavy text. Unquoted labels like `View Anticipated Testimony (Traffic Only)` can break GitHub Mermaid parsing.
- In use case diagrams, model only external roles and external systems as actors. Internal AI agents, brokers, gateways, audit subsystems, and auth/session modules belong inside the system boundary or in supporting notes, not as actors.
- When a requirements set is large, group detailed story IDs into a smaller number of externally visible capabilities to avoid arrow clutter while preserving traceability.
