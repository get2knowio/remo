"""Dynamic source registry: one agentsh approval endpoint per devcontainer.

The notifier serves many sources concurrently, one independent poll/resolve loop
per source. A source is registered for exactly as long as its presence connection
is open (spec 009). All state is in-memory (FR-001/FR-013).
"""
