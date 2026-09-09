from __future__ import annotations

import base64
import inspect
import io
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from app.services.image_vision_endpoint import VisionEndpointError, validate_vision_endpoint


class ImageEditError(RuntimeError):
    pass


_LOCAL_EDIT_MODES = {"remove_object", "replace_object", "add_object", "background"}
_ALLOWED_MODES = {"auto", *_LOCAL_EDIT_MODES, "identity_recompose"}
_REMOVE_RE = re.compile(r"\b(убер(?:и|ите)|удал(?:и|ите)|стереть|стер(?:и|ите)|remove|erase|delete)\b", re.I)
_REPLACE_RE = re.compile(r"\b(замен(?:и|ите)|поменя(?:й|йте)|replace|swap)\b", re.I)
_ADD_RE = re.compile(r"\b(добав(?:ь|ьте)|постав(?:ь|ьте)|дорису(?:й|йте)|add|insert|put)\b", re.I)
_BACKGROUND_RE = re.compile(r"\b(фон|background|задн(?:ий|ем) фон(?:е)?)\b", re.I)
_RECOMPOSE_RE = re.compile(
    r"(сделай\s+меня|посади\s+меня|помести\s+меня|я\s+за\s+рул|за\s+рул[её]м|"
    r"переодень\s+меня|make\s+me|put\s+me|place\s+me|me\s+driving|wearing)",
    re.I,
)


@dataclass(frozen=True)
class EditPlan:
    mode: str
    target: str
    target_boxes: tuple[tuple[int, int, int, int], ...]
    protected_boxes: tuple[tuple[int, int, int, int], ...]
    fill_prompt: str
    confidence: float
    planner: str

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "target": self.target,
            "target_boxes": [list(x) for x in self.target_boxes],
            "protected_boxes": [list(x) for x in self.protected_boxes],
            "fill_prompt": self.fill_prompt,
            "confidence": self.confidence,
            "planner": self.planner,
        }


def infer_edit_mode(instruction: str) -> str:
    text = " ".join(instruction.strip().split())
    if _RECOMPOSE_RE.search(text):
        return "identity_recompose"
    if _REPLACE_RE.search(text):
        return "replace_object"
    if _REMOVE_RE.search(text):
        return "remove_object"
    if _ADD_RE.search(text):
        return "add_object"
    if _BACKGROUND_RE.search(text):
        return "background"
    return "auto"


def resolve_edit_mode(requested: str, instruction: str) -> str:
    if requested not in _ALLOWED_MODES:
        raise ImageEditError("Unsupported image edit mode")
    inferred = infer_edit_mode(instruction)
    if requested == "auto":
        return inferred
    if inferred == "identity_recompose" and requested in _LOCAL_EDIT_MODES:
        raise ImageEditError("Instruction requires identity_recompose mode")
    return requested


def _normalize_box(raw) -> tuple[int, int, int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ImageEditError("Vision planner returned an invalid bounding box")
    try:
        x1, y1, x2, y2 = [int(round(float(value))) for value in raw]
    except (TypeError, ValueError) as exc:
        raise ImageEditError("Vision planner returned a non-numeric bounding box") from exc
    x1, y1 = max(0, min(999, x1)), max(0, min(999, y1))
    x2, y2 = max(1, min(1000, x2)), max(1, min(1000, y2))
    if x2 <= x1 or y2 <= y1:
        raise ImageEditError("Vision planner returned an empty bounding box")
    return x1, y1, x2, y2


def _preview_data_url(image: Image.Image, max_side: int = 1024) -> str:
    preview = image.convert("RGB")
    preview.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    preview.save(buffer, "JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class LocalImageEditVision:
    """Private edit planner and semantic QA using loopback or internal llama.cpp.

    The endpoint never identifies a person. For identity-preservation QA it only
    compares whether the visible appearance supplied by the user changed in an
    unintended way. Arbitrary remote vision endpoints are rejected.
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 45,
        *,
        trusted_internal_base_url: str = "",
        model_name: str = "local-vision",
    ):
        try:
            endpoint = validate_vision_endpoint(base_url, trusted_internal_base_url=trusted_internal_base_url)
        except VisionEndpointError as exc:
            raise ImageEditError(str(exc)) from exc
        self.url = endpoint.base_url + "/v1/chat/completions"
        self.timeout = max(5, int(timeout_seconds))
        self.model_name = str(model_name or "local-vision")[:160]
        self.endpoint_source = endpoint.source

    def _request(self, content: list[dict], *, max_tokens: int = 900) -> dict:
        payload = {
            "model": self.model_name,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                response = client.post(self.url, json=payload)
                response.raise_for_status()
                body = response.json()
            text = body["choices"][0]["message"]["content"]
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError("vision response is not an object")
            return value
        except Exception as exc:
            raise ImageEditError(f"Local image edit vision failed: {type(exc).__name__}") from exc

    def plan(self, source: Image.Image, *, instruction: str, requested_mode: str) -> EditPlan:
        instruction_text = (
            "Plan a bounded photo edit. Return JSON only: "
            '{"mode":"remove_object|replace_object|add_object|background|identity_recompose|unknown",'
            '"target":"short target description","target_boxes":[[x1,y1,x2,y2]],'
            '"protected_boxes":[[x1,y1,x2,y2]],"fill_prompt":"short positive inpaint description",'
            '"confidence":0.0}. Coordinates are 0..1000. For remove/replace locate only the object to edit. '
            "For add_object choose a plausible insertion area. protected_boxes must cover visible people/faces that the request does not ask to change. "
            "Do not invent objects that are not visible. If the target cannot be located, return mode=unknown and confidence<0.5.\n"
            f"Requested mode: {requested_mode}\nUser instruction: {instruction[:3000]}"
        )
        raw = self._request([
            {"type": "text", "text": instruction_text},
            {"type": "image_url", "image_url": {"url": _preview_data_url(source)}},
        ])
        mode = str(raw.get("mode") or "unknown")
        if mode not in {*_LOCAL_EDIT_MODES, "identity_recompose", "unknown"}:
            mode = "unknown"
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        target_boxes = tuple(_normalize_box(item) for item in (raw.get("target_boxes") or [])[:8])
        protected = tuple(_normalize_box(item) for item in (raw.get("protected_boxes") or [])[:8])
        return EditPlan(
            mode=mode,
            target=str(raw.get("target") or "")[:300],
            target_boxes=target_boxes,
            protected_boxes=protected,
            fill_prompt=str(raw.get("fill_prompt") or "")[:1000],
            confidence=confidence,
            planner=self.endpoint_source,
        )

    def verify(
        self,
        source: Image.Image,
        result: Image.Image,
        *,
        instruction: str,
        mode: str,
        preserve_identity: bool,
    ) -> dict:
        rules = (
            "Compare SOURCE and RESULT for an image editing job. Return JSON only with keys: "
            "passed:boolean, instruction_satisfied:boolean, identity_consistent:boolean, unintended_changes:boolean, "
            "findings:[{code,severity,repairable,detail}]. Check the requested edit actually happened; malformed anatomy/faces/hands; "
            "duplicate objects; broken geometry/text; lighting/perspective mismatch; and obvious unintended changes. "
            "When preserve_identity=true, compare visible facial and person-specific appearance consistency without identifying who the person is. "
            "Be strict: uncertainty means passed=false.\n"
            f"Mode: {mode}\nPreserve identity: {str(bool(preserve_identity)).lower()}\nInstruction: {instruction[:3000]}"
        )
        raw = self._request([
            {"type": "text", "text": rules},
            {"type": "text", "text": "SOURCE"},
            {"type": "image_url", "image_url": {"url": _preview_data_url(source)}},
            {"type": "text", "text": "RESULT"},
            {"type": "image_url", "image_url": {"url": _preview_data_url(result)}},
        ], max_tokens=1200)
        findings = raw.get("findings") if isinstance(raw.get("findings"), list) else []
        identity_ok = bool(raw.get("identity_consistent", not preserve_identity))
        instruction_ok = bool(raw.get("instruction_satisfied", False))
        unintended = bool(raw.get("unintended_changes", True))
        passed = bool(raw.get("passed", False)) and instruction_ok and not unintended and (identity_ok or not preserve_identity)
        return {
            "passed": passed,
            "instruction_satisfied": instruction_ok,
            "identity_consistent": identity_ok,
            "unintended_changes": unintended,
            "findings": findings[:20],
            "verifier": self.endpoint_source,
        }


def _box_to_pixels(box: tuple[int, int, int, int], width: int, height: int, padding_ratio: float) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    left = int(math.floor(x1 / 1000 * width)); top = int(math.floor(y1 / 1000 * height))
    right = int(math.ceil(x2 / 1000 * width)); bottom = int(math.ceil(y2 / 1000 * height))
    pad_x = int(max(2, (right - left) * padding_ratio)); pad_y = int(max(2, (bottom - top) * padding_ratio))
    return max(0, left - pad_x), max(0, top - pad_y), min(width, right + pad_x), min(height, bottom + pad_y)


def mask_coverage(mask: Image.Image) -> float:
    histogram = mask.convert("L").histogram()
    total = max(1, sum(histogram))
    weighted = sum(index * count for index, count in enumerate(histogram))
    return weighted / (255.0 * total)


def build_edit_mask(
    source: Image.Image,
    *,
    plan: EditPlan,
    manual_mask: Image.Image | None,
    padding_ratio: float,
    feather_px: int,
) -> Image.Image:
    width, height = source.size
    if manual_mask is not None:
        if manual_mask.size != source.size:
            raise ImageEditError("Edit mask dimensions must match the source image")
        mask = manual_mask.convert("L").point(lambda value: 255 if value >= 128 else 0)
    elif plan.mode == "background":
        mask = Image.new("L", source.size, 255)
        draw = ImageDraw.Draw(mask)
        for box in plan.protected_boxes:
            draw.rectangle(_box_to_pixels(box, width, height, 0.04), fill=0)
    else:
        if not plan.target_boxes:
            raise ImageEditError("The edit target could not be located reliably; provide a mask")
        mask = Image.new("L", source.size, 0)
        draw = ImageDraw.Draw(mask)
        for box in plan.target_boxes:
            draw.rectangle(_box_to_pixels(box, width, height, padding_ratio), fill=255)
        for box in plan.protected_boxes:
            draw.rectangle(_box_to_pixels(box, width, height, 0.02), fill=0)
    if mask.getbbox() is None:
        raise ImageEditError("Edit mask is empty")
    if feather_px > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=min(32, max(1, int(feather_px)))))
    return mask


def composite_preserving_outside(source: Image.Image, candidate: Image.Image, mask: Image.Image) -> Image.Image:
    src = source.convert("RGB")
    edited = candidate.convert("RGB").resize(src.size, Image.Resampling.LANCZOS)
    return Image.composite(edited, src, mask.convert("L"))


def outside_mask_change_score(source: Image.Image, result: Image.Image, mask: Image.Image) -> float:
    src = source.convert("RGB")
    out = result.convert("RGB").resize(src.size, Image.Resampling.LANCZOS)
    exact_outside = mask.convert("L").point(lambda value: 255 if value == 0 else 0)
    diff = ImageChops.difference(src, out)
    diff.paste((0, 0, 0), mask=ImageOps.invert(exact_outside))
    extrema = diff.getextrema()
    return float(max(channel[1] for channel in extrema))


def build_backend_prompt(instruction: str, plan: EditPlan, *, repair_findings: list[dict] | None = None) -> tuple[str, str]:
    if plan.mode == "remove_object":
        positive = plan.fill_prompt or "seamless realistic continuation of the surrounding background, matching perspective, lighting, texture and depth"
        negative = plan.target or "removed object"
    else:
        positive = instruction.strip()
        negative = "unintended changes, duplicate objects, malformed anatomy, deformed face, extra fingers, broken geometry, unreadable text"
    if repair_findings:
        short = "; ".join(str(item.get("detail") or item.get("code") or "")[:180] for item in repair_findings[:6])
        positive += "\nRepair these verified defects without changing anything else: " + short
    return positive[:6000], negative[:3000]


def _filtered_call_kwargs(callable_obj, kwargs: dict) -> dict:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return kwargs
    if any(parameter.kind == parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in signature.parameters}


class DiffusersImageEditBackend:
    """Lazy, local-only diffusers backend with explicit capability separation."""

    def __init__(self, *, inpaint_model_path: str = "", identity_model_path: str = ""):
        self.inpaint_model_path = inpaint_model_path.strip()
        self.identity_model_path = identity_model_path.strip()
        self._inpaint = None
        self._img2img = None

    @property
    def local_edit_available(self) -> bool:
        return bool(self.inpaint_model_path and Path(self.inpaint_model_path).is_dir())

    @property
    def identity_edit_available(self) -> bool:
        return bool(self.identity_model_path and Path(self.identity_model_path).is_dir())

    def _torch(self):
        try:
            import torch
        except ImportError as exc:
            raise ImageEditError("torch is unavailable in image worker") from exc
        return torch

    def _load_inpaint(self):
        if self._inpaint is not None:
            return self._inpaint
        if not self.local_edit_available:
            raise ImageEditError("Local inpainting model is not configured")
        try:
            from diffusers import AutoPipelineForInpainting
            torch = self._torch()
            dtype = torch.float16 if torch.cuda.is_available() else torch.float32
            pipe = AutoPipelineForInpainting.from_pretrained(self.inpaint_model_path, torch_dtype=dtype, local_files_only=True)
            pipe.to("cuda" if torch.cuda.is_available() else "cpu")
            pipe.set_progress_bar_config(disable=True)
            self._inpaint = pipe
            return pipe
        except Exception as exc:
            raise ImageEditError(f"Local inpainting model failed to load: {type(exc).__name__}") from exc

    def _load_identity(self):
        if self._img2img is not None:
            return self._img2img
        if not self.identity_edit_available:
            raise ImageEditError("Identity-preserving edit model is not configured")
        try:
            from diffusers import AutoPipelineForImage2Image
            torch = self._torch()
            dtype = torch.float16 if torch.cuda.is_available() else torch.float32
            pipe = AutoPipelineForImage2Image.from_pretrained(self.identity_model_path, torch_dtype=dtype, local_files_only=True)
            pipe.to("cuda" if torch.cuda.is_available() else "cpu")
            pipe.set_progress_bar_config(disable=True)
            self._img2img = pipe
            return pipe
        except Exception as exc:
            raise ImageEditError(f"Identity edit model failed to load: {type(exc).__name__}") from exc

    def edit_local(self, *, source: Image.Image, mask: Image.Image, prompt: str, negative_prompt: str, steps: int, seed: int) -> Image.Image:
        pipe = self._load_inpaint(); torch = self._torch()
        generator = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed))
        kwargs = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "image": source.convert("RGB"),
            "mask_image": mask.convert("L"),
            "num_inference_steps": int(steps),
            "generator": generator,
        }
        try:
            result = pipe(**_filtered_call_kwargs(pipe.__call__, kwargs))
            image = result.images[0]
            if not isinstance(image, Image.Image):
                raise TypeError("pipeline result is not an image")
            return image.convert("RGB")
        except Exception as exc:
            raise ImageEditError(f"Local inpainting failed: {type(exc).__name__}") from exc

    def edit_identity(self, *, source: Image.Image, prompt: str, negative_prompt: str, steps: int, seed: int) -> Image.Image:
        pipe = self._load_identity(); torch = self._torch()
        generator = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed))
        kwargs = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "image": source.convert("RGB"),
            "strength": 0.68,
            "guidance_scale": 6.0,
            "num_inference_steps": int(steps),
            "generator": generator,
        }
        try:
            result = pipe(**_filtered_call_kwargs(pipe.__call__, kwargs))
            image = result.images[0]
            if not isinstance(image, Image.Image):
                raise TypeError("pipeline result is not an image")
            return image.convert("RGB")
        except Exception as exc:
            raise ImageEditError(f"Identity scene edit failed: {type(exc).__name__}") from exc
