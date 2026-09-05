"""Search Capabilities — web and document search integrations.

SemanticSearchCapability was removed: it was a dead stub
(execute() unconditionally returned an empty result with "requires
embedding model"), never registered or imported anywhere in the
repository. Real semantic/vector search already exists and is wired in —
kernel/semantic_memory.py::SemanticMemory (Elasticsearch + Ollama
nomic-embed-text) via kernel/knowledge/sittingface_retrieval.py — this
package must not grow a second, competing implementation."""

from __future__ import annotations

from cerebellum.capability import Capability


class WebSearchCapability(Capability):
    """Web search integration using search APIs."""
    
    def __init__(self, api_key: str = "", engine: str = "google"):
        super().__init__(name='web_search')
        self._api_key = api_key
        self._engine = engine
        self._base_url = "https://www.googleapis.com/customsearch/v1" if engine == "google" else "https://api.search.brave.com/res/v1/web/search"
    
    async def execute(self, state, **kwargs):
        import httpx
        query = state.get('query', '')
        async with httpx.AsyncClient(timeout=30.0) as client:
            if self._engine == "google":
                response = await client.get(
                    self._base_url,
                    params={"key": self._api_key, "cx": state.get('search_engine_id', ''), "q": query, "num": state.get('num_results', 10)}
                )
            else:
                response = await client.get(
                    self._base_url,
                    headers={"X-Subscription-Token": self._api_key},
                    params={"q": query, "count": state.get('num_results', 10)}
                )
            return response.json()


class DocumentSearchCapability(Capability):
    """Document search using full-text search."""
    
    def __init__(self):
        super().__init__(name='document_search')
        self._documents = {}
    
    def index(self, doc_id: str, content: str, metadata: dict = None):
        self._documents[doc_id] = {"content": content, "metadata": metadata or {}}
    
    async def execute(self, state, **kwargs):
        query = state.get('query', '').lower()
        results = []
        for doc_id, doc in self._documents.items():
            if query in doc['content'].lower():
                results.append({"id": doc_id, "content": doc['content'][:200], "score": 1.0})
        return {"results": results, "count": len(results)}
