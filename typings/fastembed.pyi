from collections.abc import Iterable
from typing import Any

class TextEmbedding:
    def __init__(
        self,
        model_name: str = ...,
        cache_dir: str | None = None,
        threads: int | None = None,
        **kwargs: Any,
    ) -> None: ...
    def embed(
        self,
        documents: list[str] | Iterable[str],
        batch_size: int = ...,
        parallel: int | None = None,
        **kwargs: Any,
    ) -> Iterable[Any]: ...
