import io
from contextlib import redirect_stderr
import itertools
import numpy as np
import numpy.typing as npt
from typing import Any
from llama_cpp import Llama
from llama_cpp.llama_speculative import LlamaDraftModel
import time


class LlamaSmallModelDraft(LlamaDraftModel):
    """
    Optimized draft model for speculative decoding.

    Key Changes:
    - Removed unnecessary prints and I/O overhead.
    - Using greedy decoding parameters (top_k=1, top_p=1.0) if acceptable.
    - Using itertools.islice to grab tokens in a single step rather than a loop.
    - Consider adjusting n_ctx, n_batch, and model quantization to improve performance.
    """

    def __init__(
            self,
            model_path: str,
            num_draft_tokens: int = 5,
            temperature: float = 0.7,
            n_ctx: int = 2048,
            n_batch: int = 512,
    ):
        # Suppress unwanted stderr output during model load
        f = io.StringIO()
        with redirect_stderr(f):
            self.draft_model = Llama(
                model_path=model_path,
                n_ctx=n_ctx,
                n_batch=n_batch,
                n_gpu_layers=-1,
                flash_attn=True,
                verbose=False
            )
            print("Draft model loaded.")

        self.num_draft_tokens = num_draft_tokens
        self.temperature = temperature

    def __call__(
            self,
            input_ids: npt.NDArray[np.intc],
            /,
            **kwargs: Any
    ) -> npt.NDArray[np.intc]:
        # Convert numpy array to list for llama_cpp
        input_tokens = input_ids.tolist()

        # Generate tokens greedily or with minimal sampling complexity for speed
        generated = itertools.islice(
            self.draft_model.generate(
                tokens=input_tokens,
                temp=self.temperature,
                top_k=1,      # Greedy decoding
                top_p=1.0,    # Greedy decoding
                reset=True,   # Reset state for a fresh decode
            ),
            self.num_draft_tokens
        )

        # Collect and convert to a numpy array
        draft_tokens = np.fromiter(generated, dtype=np.intc, count=self.num_draft_tokens)
        return draft_tokens


class LlamaSmallModelDraftWithMetrics(LlamaDraftModel):
    """
    A draft model class that reports performance metrics.

    Metrics:
    - Load time of the model.
    - Per-call generation time.
    - Number of tokens generated per call.
    """

    def __init__(
            self,
            model_path: str,
            num_draft_tokens: int = 15,
            temperature: float = 0.0,
            n_ctx: int = 2048,
            n_batch: int = 512,
            verbose_metrics: bool = False
    ):
        self.verbose_metrics = verbose_metrics
        start_time = time.perf_counter()
        # Suppress unwanted stderr output during model load
        f = io.StringIO()
        with redirect_stderr(f):
            self.draft_model = Llama(
                model_path=model_path,
                n_ctx=n_ctx,
                n_batch=n_batch,
                use_mlock=True,
                n_gpu_layers=-1,
                verbose=False
            )
        self.model_load_time = time.perf_counter() - start_time

        # Store configuration
        self.num_draft_tokens = num_draft_tokens
        self.temperature = temperature

        # For metrics tracking
        self.call_count = 0
        self.total_generation_time = 0.0
        self.total_tokens_generated = 0

        if self.verbose_metrics:
            print(f"[Metrics] Draft model loaded in {self.model_load_time:.4f} seconds")

    def __call__(
            self,
            input_ids: npt.NDArray[np.intc],
            /,
            **kwargs: Any
    ) -> npt.NDArray[np.intc]:

        start_time = time.perf_counter()
        window_size = 1024
        input_tokens = input_ids[-window_size:].tolist()
        # input_tokens = input_ids.tolist()

        # Generate tokens greedily or with minimal sampling complexity for speed
        generated = itertools.islice(
            self.draft_model.generate(
                tokens=input_tokens,
                temp=self.temperature,
                top_k=1,      # Greedy decoding for speed
                top_p=1.0,    # Greedy decoding
                reset=True,   # Reset state for a fresh decode
            ),
            self.num_draft_tokens
        )

        draft_tokens = np.fromiter(generated, dtype=np.intc, count=self.num_draft_tokens)

        generation_time = time.perf_counter() - start_time
        self.call_count += 1
        self.total_generation_time += generation_time
        self.total_tokens_generated += len(draft_tokens)

        if self.verbose_metrics:
            print(f"[Metrics] Call #{self.call_count}:")
            print(f"  Generation time: {generation_time:.4f} seconds")
            print(f"  Tokens generated: {len(draft_tokens)}")
            avg_time = self.total_generation_time / self.call_count
            avg_tokens = self.total_tokens_generated / self.call_count
            print(f"  Average generation time so far: {avg_time:.4f} seconds/call")
            print(f"  Average tokens per call so far: {avg_tokens:.2f}")

        return draft_tokens

    def print_overall_metrics(self):
        if self.call_count > 0:
            avg_time = self.total_generation_time / self.call_count
            avg_tokens = self.total_tokens_generated / self.call_count
            print("[Metrics] Overall:")
            print(f"  Total calls: {self.call_count}")
            print(f"  Total generation time: {self.total_generation_time:.4f} seconds")
            print(f"  Average generation time per call: {avg_time:.4f} seconds")
            print(f"  Average tokens per call: {avg_tokens:.2f}")
        else:
            print("[Metrics] No calls made yet.")
