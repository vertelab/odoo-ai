import requests
import logging

from typing import List, Union
from langchain_core.embeddings import Embeddings

_logger = logging.getLogger(__name__)

def _format_e5(text: str, is_query: bool) -> str:
    # Recommended E5 style
    prefix = "query: " if is_query else "passage: "
    return prefix + text

class CustomBergetEmbeddings(Embeddings):
    def __init__(
        self,
        model: str,
        api_url: str,
        api_key: str,
        dimensions: Union[int, None] = None,
        user: str = "",
    ):
        self.model = model
        self.api_url = api_url
        self.api_key = api_key
        self.dimensions = dimensions
        self.user = user

    def _embed(self, texts: List[str], is_query: bool) -> List[float]:
        payload = {
            "model": self.model,
            # send list of strings if Berget supports batching; if not, loop
            "input": [_format_e5(t, is_query) for t in texts],
            "encoding_format": "float",
        }
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions
        if self.user:
            payload["user"] = self.user

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        resp = requests.post(self.build_url(), json=payload, headers=headers)
        resp.raise_for_status()
        json_data = resp.json()

        if data := json_data.get("data"):
            if len(data) > 0 and data[0].get("embedding"):
                return data[0]["embedding"]
        return []

    # Added is_query to always be False; might want to change in the future.

    def build_url(self):
        return self.api_url + "embeddings"

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._embed(texts, is_query=False)

    def embed_query(self, text: str) -> List[float]:
        return self._embed([text], is_query=False)
