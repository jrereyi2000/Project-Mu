"""Backends for local headshot generation in Project Mu.

Supports:
- MockBackend: Deterministic PIL-based rendering for tests and offline development.
- DiffusersBackend: Hugging Face diffusers integration (SDXL/SD3.5/Flux) with
  CUDA (RTX 5070 Ti 16GB), MPS, and CPU support, VRAM offload, ControlNet pose/depth
  conditioning, and IP-Adapter face-ID consistency.
- ComfyUIBackend: Client for local ComfyUI instance (http://localhost:8188) via API prompt workflow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import io
import json
import logging
import os
import random
import time
from typing import Any, Dict, Optional, Tuple, Type, Union
import uuid

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("headshots.local")


class HeadshotBackend(ABC):
    """Abstract base class for all headshot generation backends."""

    # When True, get_backend() returns one shared instance per name + config
    # instead of constructing a new one each call.
    reuse_instance = False

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config.copy() if config else {}

    @abstractmethod
    def generate(
        self,
        prompt: str,
        ref_image: Optional[bytes] = None,
        seed: Optional[int] = None,
        size: int = 1024,
        config: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        """Generate a headshot from prompt, returning valid PNG bytes.

        Args:
            prompt: Text description of the player appearance, jersey, and lighting.
            ref_image: Optional reference image PNG/JPEG bytes for face identity preservation.
            seed: Optional integer seed for reproducible generation.
            size: Dimension of output portrait in pixels (default 1024).
            config: Per-call configuration overrides.

        Returns:
            bytes: Valid PNG image data.
        """
        pass


class MockBackend(HeadshotBackend):
    """Deterministic, zero-GPU backend for testing and CI.

    Generates valid, reproducible PNG images using Pillow with deterministic
    patterns, face silhouettes, jersey blocks, and reference image compositing.
    """

    def generate(
        self,
        prompt: str,
        ref_image: Optional[bytes] = None,
        seed: Optional[int] = None,
        size: int = 1024,
        config: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        merged_config = {**self.config, **(config or {})}
        effective_seed = seed if seed is not None else 42

        # Create RNG seeded deterministically from prompt and seed
        hasher = hashlib.sha256()
        hasher.update(str(effective_seed).encode("utf-8"))
        hasher.update(prompt.encode("utf-8"))
        digest = hasher.digest()
        rng = random.Random(digest)

        # Base canvas dimensions
        width = size
        # Support optional aspect ratio from config, default square (1:1)
        aspect = merged_config.get("aspect_ratio", "1:1")
        if aspect == "4:3":
            height = int(size * 0.75)
        elif aspect == "3:4":
            height = int(size * 1.333)
        else:
            height = size

        # Studio backdrop colors
        bg_lum = rng.randint(235, 250)
        bg_color = (bg_lum, bg_lum, bg_lum + rng.randint(0, 5))
        img = Image.new("RGBA", (width, height), (*bg_color, 255))
        draw = ImageDraw.Draw(img)

        # Background subtle vignette / studio gradient circle
        center_x, center_y = width // 2, height // 2
        radius = int(min(width, height) * 0.55)
        vignette_color = (max(0, bg_lum - 15), max(0, bg_lum - 15), max(0, bg_lum - 10), 80)
        draw.ellipse(
            [center_x - radius, center_y - radius, center_x + radius, center_y + radius],
            outline=vignette_color,
            width=int(width * 0.04),
        )

        # Shoulders / Jersey block (trapezoid at bottom)
        jersey_r = rng.randint(15, 220)
        jersey_g = rng.randint(15, 220)
        jersey_b = rng.randint(15, 220)
        jersey_color = (jersey_r, jersey_g, jersey_b, 255)

        shoulder_top = int(height * 0.65)
        shoulder_width = int(width * 0.45)
        jersey_points = [
            (center_x - int(shoulder_width * 0.5), shoulder_top),
            (center_x + int(shoulder_width * 0.5), shoulder_top),
            (width, height),
            (0, height),
        ]
        draw.polygon(jersey_points, fill=jersey_color)

        # Collar / V-neck
        collar_color = (255, 255, 255, 255)
        vneck_points = [
            (center_x - int(shoulder_width * 0.2), shoulder_top),
            (center_x, int(shoulder_top + height * 0.1)),
            (center_x + int(shoulder_width * 0.2), shoulder_top),
        ]
        draw.polygon(vneck_points, fill=collar_color)

        # Neck
        neck_width = int(width * 0.16)
        neck_top = int(height * 0.52)
        neck_bottom = shoulder_top + 10
        neck_color = (rng.randint(90, 210), rng.randint(70, 160), rng.randint(50, 120), 255)
        draw.rectangle(
            [center_x - neck_width // 2, neck_top, center_x + neck_width // 2, neck_bottom],
            fill=neck_color,
        )

        # Head / Face oval
        face_rx = int(width * 0.18)
        face_ry = int(height * 0.22)
        face_cy = int(height * 0.42)
        draw.ellipse(
            [center_x - face_rx, face_cy - face_ry, center_x + face_rx, face_cy + face_ry],
            fill=neck_color,
            outline=(max(0, neck_color[0] - 25), max(0, neck_color[1] - 25), max(0, neck_color[2] - 25), 255),
            width=2,
        )

        # Hair cap
        hair_color = (rng.randint(10, 45), rng.randint(10, 35), rng.randint(10, 30), 255)
        draw.arc(
            [center_x - face_rx - 4, face_cy - face_ry - 4, center_x + face_rx + 4, face_cy + 10],
            start=170,
            end=370,
            fill=hair_color,
            width=int(width * 0.03),
        )

        # Eyes & Smile
        eye_y = face_cy - int(face_ry * 0.15)
        eye_dx = int(face_rx * 0.4)
        eye_radius = int(width * 0.015)
        draw.ellipse(
            [center_x - eye_dx - eye_radius, eye_y - eye_radius, center_x - eye_dx + eye_radius, eye_y + eye_radius],
            fill=(20, 20, 20, 255),
        )
        draw.ellipse(
            [center_x + eye_dx - eye_radius, eye_y - eye_radius, center_x + eye_dx + eye_radius, eye_y + eye_radius],
            fill=(20, 20, 20, 255),
        )

        # Mouth / smile
        mouth_y = face_cy + int(face_ry * 0.4)
        mouth_w = int(face_rx * 0.35)
        draw.arc(
            [center_x - mouth_w, mouth_y - 10, center_x + mouth_w, mouth_y + 10],
            start=10,
            end=170,
            fill=(100, 30, 30, 255),
            width=2,
        )

        # IP-Adapter / face reference badge if provided
        if ref_image:
            try:
                ref_pil = Image.open(io.BytesIO(ref_image)).convert("RGBA")
                thumb_size = max(64, int(width * 0.16))
                ref_pil.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)
                badge_x = int(width * 0.04)
                badge_y = int(height * 0.04)
                # Outer border
                draw.rectangle(
                    [badge_x - 3, badge_y - 3, badge_x + ref_pil.width + 3, badge_y + ref_pil.height + 3],
                    fill=(40, 40, 40, 200),
                )
                img.paste(ref_pil, (badge_x, badge_y), ref_pil if ref_pil.mode == "RGBA" else None)
                draw.text(
                    (badge_x, badge_y + ref_pil.height + 4),
                    "IP-ADAPTER REF",
                    fill=(10, 150, 100, 255),
                )
            except Exception as e:
                logger.warning("MockBackend could not composite ref_image: %s", e)

        # ControlNet pose indicator if enabled in config
        if merged_config.get("controlnet") or merged_config.get("controlnet_model"):
            draw.text(
                (int(width * 0.04), height - int(height * 0.08)),
                "[ControlNet: Pose & Depth Active]",
                fill=(0, 120, 200, 255),
            )

        # Watermark/metadata banner at top
        meta_text = f"[MockBackend] Seed={effective_seed} Size={width}x{height}"
        draw.text((int(width * 0.04), int(height * 0.94)), meta_text, fill=(80, 80, 80, 200))

        # Convert to RGB and save as PNG bytes
        out_rgb = img.convert("RGB")
        buf = io.BytesIO()
        out_rgb.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


class DiffusersBackend(HeadshotBackend):
    """Hugging Face diffusers backend for local execution.

    Target hardware:
    - PC: NVIDIA RTX 5070 Ti (16GB VRAM, CUDA, fp16/bf16 with CPU offload)
    - Mac: Apple Silicon MPS (fp16)
    - Fallback: CPU (fp32)

    Features:
    - ControlNet hook: pose & depth conditioning for uniform portrait framing.
    - IP-Adapter hook: face identity reference conditioning across seasons.
    - VRAM optimizations: enable_model_cpu_offload, vae_slicing, vae_tiling.
    """

    # Loading SDXL takes tens of seconds, so get_backend() reuses one instance
    # (and its loaded pipeline) per backend name + config.
    reuse_instance = True

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.model_id = (
            self.config.get("model_id")
            or os.environ.get("LOCAL_HEADSHOT_MODEL_ID")
            or "SG161222/RealVisXL_V5.0"
        )
        self.device = self._detect_device()
        self.torch_dtype = self._detect_dtype()
        self._pipe = None
        self._controlnet = None
        # Pipeline state the loaded pipe was built with. None means unknown
        # (pipe assigned from outside), in which case it is used as-is.
        self._pipe_controlnet: Optional[bool] = None
        self._ip_adapter_loaded = False

    def _detect_device(self) -> str:
        override = self.config.get("device")
        if override:
            return override
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def _detect_dtype(self) -> Any:
        try:
            import torch
            if self.device == "cuda":
                # SDXL and CivitAI models provide native fp16 variants
                use_bf16 = self.config.get("use_bfloat16", False)
                return torch.bfloat16 if use_bf16 and hasattr(torch, "bfloat16") else torch.float16
            elif self.device == "mps":
                return torch.float16
            return torch.float32
        except ImportError:
            return None

    def _init_pipeline(
        self,
        use_controlnet: bool = False,
        use_ip_adapter: bool = False,
    ) -> Any:
        """Lazily initialize diffusers pipeline with VRAM optimizations."""
        try:
            import torch
            from diffusers import (
                AutoPipelineForText2Image,
                StableDiffusionXLPipeline,
                StableDiffusionXLControlNetPipeline,
                ControlNetModel,
            )
        except ImportError as e:
            raise ImportError(
                "diffusers and torch must be installed to use DiffusersBackend. "
                "Install with: pip install diffusers torch transformers accelerate"
            ) from e

        controlnet_id = self.config.get("controlnet_model", "diffusers/controlnet-depth-sdxl-1.0")

        if use_controlnet:
            logger.info("Loading ControlNet model %s on %s...", controlnet_id, self.device)
            controlnet = ControlNetModel.from_pretrained(
                controlnet_id,
                torch_dtype=self.torch_dtype,
            )
            pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
                self.model_id,
                controlnet=controlnet,
                torch_dtype=self.torch_dtype,
                variant="fp16" if self.torch_dtype == torch.float16 else None,
            )
        else:
            logger.info("Loading text-to-image pipeline %s on %s...", self.model_id, self.device)
            try:
                pipe = StableDiffusionXLPipeline.from_pretrained(
                    self.model_id,
                    torch_dtype=self.torch_dtype,
                    variant="fp16" if self.torch_dtype == torch.float16 else None,
                )
            except Exception:
                pipe = AutoPipelineForText2Image.from_pretrained(
                    self.model_id,
                    torch_dtype=self.torch_dtype,
                    variant="fp16" if self.torch_dtype == torch.float16 else None,
                )

        # Attach DPM-Solver++ with Karras sigmas for optimal photorealism if available
        if hasattr(pipe, "scheduler") and hasattr(pipe.scheduler, "config"):
            try:
                from diffusers import DPMSolverMultistepScheduler
                pipe.scheduler = DPMSolverMultistepScheduler.from_config(
                    pipe.scheduler.config, use_karras_sigmas=True
                )
            except Exception as e:
                logger.debug("Could not attach DPMSolverMultistepScheduler: %s", e)

        # VRAM Optimizations for 16GB cards (§7.7)
        enable_cpu_offload = self.config.get("enable_model_cpu_offload", True)
        if enable_cpu_offload and self.device == "cuda":
            pipe.enable_model_cpu_offload()
        elif self.device != "cpu":
            pipe.to(self.device)

        if hasattr(pipe, "enable_vae_slicing"):
            pipe.enable_vae_slicing()
        if hasattr(pipe, "enable_vae_tiling"):
            pipe.enable_vae_tiling()

        self._pipe_controlnet = use_controlnet
        self._ip_adapter_loaded = False

        # IP-Adapter initialization if requested
        if use_ip_adapter:
            self._load_ip_adapter(pipe)

        return pipe

    def _load_ip_adapter(self, pipe: Any) -> None:
        """Load IP-Adapter face identity weights into an existing pipeline."""
        ip_repo = self.config.get("ip_adapter_repo", "h94/IP-Adapter")
        ip_subfolder = self.config.get("ip_adapter_subfolder", "sdxl_models")
        ip_weight = self.config.get("ip_adapter_weight_name", "ip-adapter-plus-face_sdxl_vit-h.safetensors")
        logger.info("Loading IP-Adapter face identity weights from %s...", ip_repo)
        pipe.load_ip_adapter(ip_repo, subfolder=ip_subfolder, weight_name=ip_weight)
        ip_scale = self.config.get("ip_adapter_scale", 0.7)
        pipe.set_ip_adapter_scale(ip_scale)
        self._ip_adapter_loaded = True

    def _ensure_pipeline(self, use_controlnet: bool, use_ip_adapter: bool) -> None:
        """Build the pipeline, or adjust the cached one, to match this call's needs.

        A pipeline with IP-Adapter loaded errors when called without an
        ip_adapter_image, and one without it cannot take a face reference, so
        the adapter is loaded/unloaded per call. A ControlNet change needs a
        different pipeline class, so the pipeline is rebuilt.
        """
        if self._pipe is not None and self._pipe_controlnet is not None \
                and self._pipe_controlnet != use_controlnet:
            logger.info("ControlNet setting changed; rebuilding diffusers pipeline.")
            self._pipe = None

        if self._pipe is None:
            self._pipe = self._init_pipeline(
                use_controlnet=use_controlnet,
                use_ip_adapter=use_ip_adapter,
            )
            return

        if use_ip_adapter and not self._ip_adapter_loaded:
            self._load_ip_adapter(self._pipe)
        elif not use_ip_adapter and self._ip_adapter_loaded:
            logger.info("Unloading IP-Adapter (no face reference for this call).")
            self._pipe.unload_ip_adapter()
            self._ip_adapter_loaded = False

    def generate(
        self,
        prompt: str,
        ref_image: Optional[bytes] = None,
        seed: Optional[int] = None,
        size: int = 1024,
        config: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        merged_config = {**self.config, **(config or {})}
        use_controlnet = bool(merged_config.get("controlnet") or merged_config.get("controlnet_model"))
        # IP-Adapter needs an image on every call, so it is only active with a
        # ref_image; config use_ip_adapter=False turns it off even then.
        use_ip_adapter = bool(ref_image) and merged_config.get("use_ip_adapter", True) is not False

        self._ensure_pipeline(use_controlnet=use_controlnet, use_ip_adapter=use_ip_adapter)

        try:
            import torch
        except ImportError as e:
            raise ImportError("torch is required for generation.") from e

        # Generator & seed setup
        gen_device = "cpu" if self.device == "mps" else self.device
        generator = torch.Generator(device=gen_device)
        if seed is not None:
            generator.manual_seed(seed)

        default_negative = (
            "helmet, football, ball, full body, lower body, legs, feet, cleats, pants, "
            "hands, gloves, pads, gear, collage, split screen, triptych, turnaround, "
            "character sheet, multiple views, multiple people, multiple angles, stadium, "
            "blurry, distorted face, bad anatomy, deformed eyes, text, watermark"
        )
        neg_prompt = merged_config.get("negative_prompt", default_negative)
        use_compel = merged_config.get("use_compel", True)

        prompt_embeds = None
        pooled_prompt_embeds = None
        neg_prompt_embeds = None
        neg_pooled_prompt_embeds = None

        if use_compel and hasattr(self._pipe, "tokenizer_2") and hasattr(self._pipe, "text_encoder_2"):
            try:
                compel_device = self.device if self.device != "mps" else "cpu"
                try:
                    from compel import CompelForSDXL
                    compel_sdxl = CompelForSDXL(self._pipe, device=compel_device)
                    cond = compel_sdxl(main_prompt=prompt, negative_prompt=neg_prompt)
                    prompt_embeds = cond.embeds
                    pooled_prompt_embeds = cond.pooled_embeds
                    neg_prompt_embeds = cond.negative_embeds
                    neg_pooled_prompt_embeds = cond.negative_pooled_embeds
                except (ImportError, Exception):
                    from compel import Compel, ReturnedEmbeddingsType
                    compel = Compel(
                        tokenizer=[self._pipe.tokenizer, self._pipe.tokenizer_2],
                        text_encoder=[self._pipe.text_encoder, self._pipe.text_encoder_2],
                        returned_embeddings_type=ReturnedEmbeddingsType.PENULTIMATE_HIDDEN_STATES_NON_NORMALIZED,
                        requires_pooled=[False, True],
                        truncate_long_prompts=False,
                        device=compel_device,
                    )
                    prompt_embeds, pooled_prompt_embeds = compel(prompt)
                    neg_prompt_embeds, neg_pooled_prompt_embeds = compel(neg_prompt)
                    # truncate_long_prompts=False leaves the two at different token
                    # lengths; diffusers requires matching shapes.
                    prompt_embeds, neg_prompt_embeds = compel.pad_conditioning_tensors_to_same_length(
                        [prompt_embeds, neg_prompt_embeds]
                    )
            except Exception as e:
                logger.debug("Compel long-prompt encoding not active or failed (%s), falling back to raw prompt text.", e)

        call_kwargs: Dict[str, Any] = {
            "height": size,
            "width": size,
            "num_inference_steps": merged_config.get("steps", 30),
            "guidance_scale": merged_config.get("guidance_scale", 5.0),
            "generator": generator,
        }

        if prompt_embeds is not None and pooled_prompt_embeds is not None:
            call_kwargs["prompt_embeds"] = prompt_embeds
            call_kwargs["pooled_prompt_embeds"] = pooled_prompt_embeds
            if neg_prompt_embeds is not None and neg_pooled_prompt_embeds is not None:
                call_kwargs["negative_prompt_embeds"] = neg_prompt_embeds
                call_kwargs["negative_pooled_prompt_embeds"] = neg_pooled_prompt_embeds
        else:
            call_kwargs["prompt"] = prompt
            call_kwargs["negative_prompt"] = neg_prompt

        # IP-Adapter face reference conditioning
        if use_ip_adapter and self._ip_adapter_loaded:
            ref_pil = Image.open(io.BytesIO(ref_image)).convert("RGB")
            call_kwargs["ip_adapter_image"] = ref_pil

        # ControlNet pose/depth conditioning hook
        if use_controlnet:
            control_input = merged_config.get("controlnet_image")
            if isinstance(control_input, bytes):
                control_pil = Image.open(io.BytesIO(control_input)).convert("RGB")
            elif isinstance(control_input, Image.Image):
                control_pil = control_input
            else:
                # Fallback: create standard canonical headshot depth/pose map
                control_pil = Image.new("RGB", (size, size), (128, 128, 128))
            call_kwargs["image"] = control_pil
            call_kwargs["controlnet_conditioning_scale"] = merged_config.get(
                "controlnet_conditioning_scale", 0.75
            )

        output = self._pipe(**call_kwargs)
        result_img: Image.Image = output.images[0]

        buf = io.BytesIO()
        result_img.save(buf, format="PNG")
        return buf.getvalue()


class ComfyUIBackend(HeadshotBackend):
    """Client backend connecting to a local ComfyUI instance (http://localhost:8188).

    Executes headshot workflows via ComfyUI's REST API:
    1. Optional /upload/image for ref_image face conditioning.
    2. POST /prompt with parameterized node graph (SDXL/Flux + ControlNet/IPAdapter).
    3. Polls /history/{prompt_id} until image generation completes.
    4. Downloads resulting PNG from GET /view.
    """

    DEFAULT_HOST = "http://127.0.0.1:8188"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.host = (
            self.config.get("host")
            or os.environ.get("COMFYUI_HOST")
            or self.DEFAULT_HOST
        ).rstrip("/")
        self.client_id = str(uuid.uuid4())
        self.timeout = self.config.get("timeout", 120.0)
        self.poll_interval = self.config.get("poll_interval", 0.5)

    def _build_workflow(
        self,
        prompt: str,
        seed: int,
        size: int,
        ref_image_name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Construct standard ComfyUI API prompt graph with SDXL and optional IP-Adapter/ControlNet."""
        cfg = config or {}
        checkpoint = cfg.get("checkpoint", "sd_xl_base_1.0.safetensors")
        steps = cfg.get("steps", 25)
        cfg_scale = cfg.get("cfg_scale", 6.5)
        sampler_name = cfg.get("sampler_name", "dpmpp_2m")
        scheduler = cfg.get("scheduler", "karras")

        workflow: Dict[str, Any] = {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "cfg": cfg_scale,
                    "denoise": 1.0,
                    "latent_image": ["5", 0],
                    "model": ["4", 0],
                    "negative": ["7", 0],
                    "positive": ["6", 0],
                    "sampler_name": sampler_name,
                    "scheduler": scheduler,
                    "seed": seed,
                    "steps": steps,
                },
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": checkpoint},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"batch_size": 1, "height": size, "width": size},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["4", 1],
                    "text": prompt,
                },
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["4", 1],
                    "text": cfg.get(
                        "negative_prompt",
                        "blurry, ugly, distorted, helmet, watermark, low quality",
                    ),
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "Mu_Headshot", "images": ["8", 0]},
            },
        }

        # IP-Adapter face hook if ref image is present
        if ref_image_name:
            workflow["10"] = {
                "class_type": "LoadImage",
                "inputs": {"image": ref_image_name},
            }
            workflow["11"] = {
                "class_type": "IPAdapterModelLoader",
                "inputs": {"ipadapter_file": cfg.get("ipadapter_file", "ip-adapter-plus-face_sdxl.safetensors")},
            }
            workflow["12"] = {
                "class_type": "IPAdapterApply",
                "inputs": {
                    "model": ["4", 0],
                    "ipadapter": ["11", 0],
                    "image": ["10", 0],
                    "weight": cfg.get("ipadapter_weight", 0.75),
                },
            }
            # Rewire KSampler to use IP-Adapter model
            workflow["3"]["inputs"]["model"] = ["12", 0]

        # ControlNet pose hook if enabled
        if cfg.get("controlnet"):
            workflow["13"] = {
                "class_type": "ControlNetLoader",
                "inputs": {"control_net_name": cfg.get("controlnet_name", "control-lora-depth-rank256.safetensors")},
            }
            workflow["14"] = {
                "class_type": "ControlNetApply",
                "inputs": {
                    "conditioning": ["6", 0],
                    "control_net": ["13", 0],
                    "image": ["10", 0] if ref_image_name else ["5", 0],
                    "strength": cfg.get("controlnet_strength", 0.7),
                },
            }
            # Rewire positive conditioning to ControlNet output
            workflow["3"]["inputs"]["positive"] = ["14", 0]

        return workflow

    def _upload_ref_image(self, ref_image: bytes) -> str:
        """Upload reference image bytes to ComfyUI /upload/image endpoint."""
        import requests

        filename = f"ref_{uuid.uuid4().hex[:8]}.png"
        files = {"image": (filename, ref_image, "image/png")}
        data = {"overwrite": "true"}

        url = f"{self.host}/upload/image"
        try:
            resp = requests.post(url, files=files, data=data, timeout=30.0)
            resp.raise_for_status()
            res_json = resp.json()
            return res_json.get("name", filename)
        except requests.exceptions.RequestException as e:
            raise ConnectionError(
                f"Failed to upload reference image to ComfyUI at {url}: {e}"
            ) from e

    def generate(
        self,
        prompt: str,
        ref_image: Optional[bytes] = None,
        seed: Optional[int] = None,
        size: int = 1024,
        config: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        import requests

        merged_config = {**self.config, **(config or {})}
        effective_seed = seed if seed is not None else random.randint(1, 2**31 - 1)

        # Upload ref image if provided
        ref_image_name = None
        if ref_image:
            ref_image_name = self._upload_ref_image(ref_image)

        # Build workflow
        workflow = self._build_workflow(
            prompt=prompt,
            seed=effective_seed,
            size=size,
            ref_image_name=ref_image_name,
            config=merged_config,
        )

        # Submit prompt to /prompt
        prompt_url = f"{self.host}/prompt"
        payload = {"prompt": workflow, "client_id": self.client_id}
        try:
            post_resp = requests.post(prompt_url, json=payload, timeout=30.0)
            post_resp.raise_for_status()
            post_data = post_resp.json()
            prompt_id = post_data["prompt_id"]
        except requests.exceptions.RequestException as e:
            raise ConnectionError(
                f"Cannot connect to ComfyUI at {prompt_url}. "
                f"Ensure ComfyUI is running on {self.host}. Details: {e}"
            ) from e

        # Poll /history/{prompt_id} until execution is complete
        history_url = f"{self.host}/history/{prompt_id}"
        start_time = time.time()
        output_images = []

        while time.time() - start_time < self.timeout:
            try:
                hist_resp = requests.get(history_url, timeout=10.0)
                hist_resp.raise_for_status()
                hist_data = hist_resp.json()
            except requests.exceptions.RequestException as e:
                logger.warning("Error polling ComfyUI history: %s", e)
                time.sleep(self.poll_interval)
                continue

            if prompt_id in hist_data:
                outputs = hist_data[prompt_id].get("outputs", {})
                for node_id, node_out in outputs.items():
                    if "images" in node_out:
                        output_images = node_out["images"]
                        break
                if output_images:
                    break

            time.sleep(self.poll_interval)

        if not output_images:
            raise TimeoutError(
                f"ComfyUI prompt {prompt_id} timed out after {self.timeout}s without producing an image."
            )

        # Download the first generated image from /view
        img_info = output_images[0]
        view_url = f"{self.host}/view"
        params = {
            "filename": img_info["filename"],
            "subfolder": img_info.get("subfolder", ""),
            "type": img_info.get("type", "output"),
        }

        try:
            view_resp = requests.get(view_url, params=params, timeout=30.0)
            view_resp.raise_for_status()
            return view_resp.content
        except requests.exceptions.RequestException as e:
            raise RuntimeError(
                f"Failed to download image {params} from ComfyUI: {e}"
            ) from e


# Backend Registry
_BACKEND_REGISTRY: Dict[str, Type[HeadshotBackend]] = {
    "mock": MockBackend,
    "diffusers": DiffusersBackend,
    "comfyui": ComfyUIBackend,
}


_INSTANCE_CACHE: Dict[Tuple[str, str], HeadshotBackend] = {}


def register_backend(name: str, backend_cls: Type[HeadshotBackend]) -> None:
    """Register a new headshot backend class."""
    _BACKEND_REGISTRY[name.lower()] = backend_cls
    for key in [k for k in _INSTANCE_CACHE if k[0] == name.lower()]:
        del _INSTANCE_CACHE[key]


def clear_backend_cache() -> None:
    """Drop cached backend instances (and any pipelines they hold)."""
    _INSTANCE_CACHE.clear()


def get_backend(
    backend: Union[str, HeadshotBackend, None] = None,
    config: Optional[Dict[str, Any]] = None,
) -> HeadshotBackend:
    """Retrieve or instantiate a HeadshotBackend instance."""
    if isinstance(backend, HeadshotBackend):
        return backend

    backend_name = backend
    if backend_name is None:
        backend_name = os.environ.get("LOCAL_HEADSHOT_BACKEND", "mock")

    backend_key = str(backend_name).lower()
    if backend_key not in _BACKEND_REGISTRY:
        valid_backends = list(_BACKEND_REGISTRY.keys())
        raise ValueError(
            f"Unknown headshot backend '{backend_name}'. "
            f"Available backends: {valid_backends}"
        )

    cls = _BACKEND_REGISTRY[backend_key]
    if not cls.reuse_instance:
        return cls(config=config)

    cache_key = (backend_key, json.dumps(config or {}, sort_keys=True, default=repr))
    instance = _INSTANCE_CACHE.get(cache_key)
    if instance is None:
        instance = cls(config=config)
        _INSTANCE_CACHE[cache_key] = instance
    return instance
