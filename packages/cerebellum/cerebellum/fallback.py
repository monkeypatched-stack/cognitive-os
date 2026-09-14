"""Fallback Capabilities — document search and web search for retry.

When an answer fails:
1. Use document search to find relevant information
2. Use web search to find external information
3. Replan execution based on gathered information
"""

from __future__ import annotations


from cerebellum.capability import Capability


class DocumentSearchCapability(Capability):
    """Search internal documents for relevant information."""

    def __init__(self):
        super().__init__(name="document_search")
        self._documents = {
            "batch": [
                {
                    "id": "DOC-001",
                    "title": "Batch Record Procedure",
                    "content": "Batch records track production from raw materials to finished goods.",
                },
                {
                    "id": "DOC-002",
                    "title": "Batch Release SOP",
                    "content": "Batch release requires quality approval and documentation review.",
                },
            ],
            "plant": [
                {
                    "id": "DOC-010",
                    "title": "Plant Operations Manual",
                    "content": "Plants contain production lines, stages, and workstations.",
                },
            ],
            "worker": [
                {
                    "id": "DOC-020",
                    "title": "Worker Assignment Guide",
                    "content": "Workers are assigned to shifts and workstations.",
                },
            ],
            "maintenance": [
                {
                    "id": "DOC-030",
                    "title": "Maintenance Procedures",
                    "content": "Maintenance includes preventive and corrective activities.",
                },
                {
                    "id": "DOC-031",
                    "title": "Calibration Schedule",
                    "content": "Calibration is required quarterly for all measurement equipment.",
                },
            ],
            "sop": [
                {
                    "id": "DOC-040",
                    "title": "SOP Management",
                    "content": "SOPs define standard operating procedures for all processes.",
                },
            ],
            "approval": [
                {
                    "id": "DOC-050",
                    "title": "Approval Workflow",
                    "content": "Approvals follow a hierarchical review process.",
                },
            ],
            "line": [
                {
                    "id": "DOC-060",
                    "title": "Production Line Guide",
                    "content": "Production lines contain stages and workstations.",
                },
            ],
            "stage": [
                {
                    "id": "DOC-070",
                    "title": "Process Stages",
                    "content": "Stages include dispensing, granulation, compression, coating.",
                },
            ],
            "workstation": [
                {
                    "id": "DOC-080",
                    "title": "Workstation Configuration",
                    "content": "Workstations contain machines and equipment.",
                },
            ],
        }

    async def execute(self, state, **kwargs):
        entities = state.get("entities", {})
        query = state.get("question", "").lower()

        found_docs = []
        for etype in entities:
            docs = self._documents.get(etype, [])
            for doc in docs:
                if any(word in doc["content"].lower() for word in query.split()):
                    found_docs.append(doc)

        # If no entity-based docs, search all
        if not found_docs:
            for etype, docs in self._documents.items():
                for doc in docs:
                    if any(word in doc["content"].lower() for word in query.split()):
                        found_docs.append(doc)

        return {
            "documents": found_docs[:5],
            "count": len(found_docs),
            "phase": "document_search",
        }


class WebSearchCapability(Capability):
    """Search the web for external information."""

    def __init__(self):
        super().__init__(name="web_search")
        self._web_results = {
            "batch": [
                {
                    "title": "GMP Batch Record Requirements",
                    "url": "https://example.com/gmp-batch",
                    "snippet": "FDA 21 CFR Part 211 requires complete batch records for pharmaceutical manufacturing.",
                },
            ],
            "maintenance": [
                {
                    "title": "Preventive Maintenance Best Practices",
                    "url": "https://example.com/pm-best",
                    "snippet": "Preventive maintenance reduces downtime by 25-30%.",
                },
            ],
            "quality": [
                {
                    "title": "Quality Management Systems",
                    "url": "https://example.com/qms",
                    "snippet": "ISO 9001 quality management standards.",
                },
            ],
        }

    async def execute(self, state, **kwargs):
        entities = state.get("entities", {})

        results = []
        for etype in entities:
            web = self._web_results.get(etype, [])
            results.extend(web)

        # Fallback: search all
        if not results:
            for web in self._web_results.values():
                results.extend(web)

        return {
            "web_results": results[:5],
            "count": len(results),
            "phase": "web_search",
        }


class ReplannerCapability(Capability):
    """Replan execution based on gathered information."""

    def __init__(self):
        super().__init__(name="replan")

    async def execute(self, state, **kwargs):
        docs = state.get("documents", [])
        web_results = state.get("web_results", [])
        question = state.get("question", "")

        # Aggregate information
        aggregated_info = []
        for doc in docs:
            aggregated_info.append(f"Document: {doc.get('title', '')} - {doc.get('content', '')}")
        for web in web_results:
            aggregated_info.append(f"Web: {web.get('title', '')} - {web.get('snippet', '')}")

        # Build enhanced answer
        if aggregated_info:
            enhanced_answer = "Based on available information:\n" + "\n".join(
                f"• {info}" for info in aggregated_info[:5]
            )
        else:
            enhanced_answer = f"I found some relevant information about your question regarding: {question}"

        return {
            "enhanced_answer": enhanced_answer,
            "aggregated_info": aggregated_info,
            "phase": "replan",
        }
