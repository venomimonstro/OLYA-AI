from __future__ import annotations

import inspect
import json
from pathlib import Path

from PIL import Image

from app.services.image_editing import ImageEditError


class QwenImageEditBackend:
    """Local-files-only adapter for Qwen-Image-Edit / Qwen-Image-Edit-2509.

    The adapter deliberately exposes the same two operations used by the edit
    runtime. Local-object edits still run through server-side mask compositing,
    while identity recomposition may use the 2509 Plus pipeline's multi-image
    input (identity reference + scene/source image).
    """

    def __init__(self, *, model_path: str, identity_model_path: str = "", require_cuda: bool = True):
        self.model_path = str(model_path or "").strip()
        self.identity_model_path = str(identity_model_path or self.model_path).strip()
        self.require_cuda = bool(require_cuda)
        self._local = None
        self._identity = None
        self._local_class = ""
        self._identity_class = ""

    def _validate_path(self, value: str) -> tuple[Path, str]:
        path = Path(value)
        if not value or not path.is_dir():
            raise ImageEditError("Qwen Image Edit checkpoint directory is not configured")
        model_index = path / "model_index.json"
        if not model_index.is_file():
            raise ImageEditError("Qwen Image Edit checkpoint is missing model_index.json")
        try:
            metadata = json.loads(model_index.read_text("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ImageEditError("Qwen Image Edit model_index.json is invalid") from exc
        class_name = str(metadata.get("_class_name") or "")
        if "QwenImageEdit" not in class_name:
            raise ImageEditError("Configured checkpoint is not a Qwen Image Edit pipeline")
        return path, class_name

    @staticmethod
    def _torch():
        try:
            import torch
        except ImportError as exc:
            raise ImageEditError("torch is unavailable in image worker") from exc
        return torch

    def _load(self, path_value: str):
        path, class_name = self._validate_path(path_value)
        try:
            from diffusers import DiffusionPipeline
            torch = self._torch()
            if self.require_cuda and not torch.cuda.is_available():
                raise ImageEditError("Qwen Image Edit requires a CUDA image worker in the current production profile")
            dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
            kwargs = {"torch_dtype": dtype, "local_files_only": True}
            # device_map avoids a second full copy for large/quantized checkpoints.
            if torch.cuda.is_available():
                kwargs["device_map"] = "cuda"
            pipe = DiffusionPipeline.from_pretrained(str(path), **kwargs)
            if not torch.cuda.is_available():
                pipe.to("cpu")
            pipe.set_progress_bar_config(disable=True)
            return pipe, class_name
        except ImageEditError:
            raise
        except Exception as exc:
            raise ImageEditError(f"Qwen Image Edit checkpoint failed to load: {type(exc).__name__}") from exc

    def _local_pipe(self):
        if self._local is None:
            self._local, self._local_class = self._load(self.model_path)
        return self._local

    def _identity_pipe(self):
        if self._identity is None:
            # Reuse the loaded object when both capabilities point at exactly the
            # same checkpoint. This matters for 20B-class edit models.
            if self.identity_model_path == self.model_path and self._local is not None:
                self._identity, self._identity_class = self._local, self._local_class
            else:
                self._identity, self._identity_class = self._load(self.identity_model_path)
        return self._identity

    @staticmethod
    def _filtered_kwargs(callable_obj, values: dict) -> dict:
        try:
            signature = inspect.signature(callable_obj)
        except (TypeError, ValueError):
            return values
        if any(p.kind == p.VAR_KEYWORD for p in signature.parameters.values()):
            return values
        return {key: value for key, value in values.items() if key in signature.parameters}

    def _generator(self, seed: int):
        torch = self._torch()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        return torch.Generator(device=device).manual_seed(int(seed))

    @staticmethod
    def _result_image(result) -> Image.Image:
        images = getattr(result, "images", None)
        if not images or not isinstance(images[0], Image.Image):
            raise ImageEditError("Qwen Image Edit returned no image")
        return images[0].convert("RGB")

    def edit_local(self, *, source: Image.Image, mask: Image.Image, prompt: str, negative_prompt: str, steps: int, seed: int) -> Image.Image:
        # Qwen performs instruction-based appearance editing. The mask is enforced
        # after generation by server-side compositing, so Qwen cannot modify exact
        # pixels outside the allowed region even though it sees the whole source.
        _ = mask
        pipe = self._local_pipe()
        kwargs = {
            "image": source.convert("RGB"),
            "prompt": prompt,
            "negative_prompt": negative_prompt or " ",
            "num_inference_steps": int(steps),
            "generator": self._generator(seed),
            "true_cfg_scale": 4.0,
            "guidance_scale": 1.0,
        }
        try:
            result = pipe(**self._filtered_kwargs(pipe.__call__, kwargs))
            return self._result_image(result)
        except ImageEditError:
            raise
        except Exception as exc:
            raise ImageEditError(f"Qwen local image edit failed: {type(exc).__name__}") from exc

    def edit_identity(
        self,
        *,
        source: Image.Image,
        prompt: str,
        negative_prompt: str,
        steps: int,
        seed: int,
        identity_reference: Image.Image | None = None,
    ) -> Image.Image:
        pipe = self._identity_pipe()
        identity = (identity_reference or source).convert("RGB")
        scene = source.convert("RGB")
        same_pixels = identity.size == scene.size and identity.tobytes() == scene.tobytes()
        if same_pixels:
            image_input = identity
        else:
            if "Plus" not in self._identity_class:
                raise ImageEditError("Configured Qwen Image Edit checkpoint does not support multi-image identity + scene editing")
            image_input = [identity, scene]
            prompt = (
                "Use image 1 as the person identity/appearance reference and image 2 as the source scene/reference. "
                "Preserve the visible identity and natural anatomy while following this edit instruction: " + prompt
            )
        kwargs = {
            "image": image_input,
            "prompt": prompt,
            "negative_prompt": negative_prompt or " ",
            "num_inference_steps": int(steps),
            "generator": self._generator(seed),
            "true_cfg_scale": 4.0,
            "guidance_scale": 1.0,
        }
        try:
            result = pipe(**self._filtered_kwargs(pipe.__call__, kwargs))
            return self._result_image(result)
        except ImageEditError:
            raise
        except Exception as exc:
            raise ImageEditError(f"Qwen identity image edit failed: {type(exc).__name__}") from exc
