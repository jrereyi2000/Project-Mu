"""Unit and integration tests for local headshot generation pipeline.

Tests:
- MockBackend: PNG validity, seed determinism, ref_image compositing, ControlNet config.
- Generator interface: backend switching, validation, error handling.
- DiffusersBackend: device detection (CUDA/MPS/CPU), VRAM offload, ControlNet & IP-Adapter hooks.
- ComfyUIBackend: workflow construction, prompt submission, history polling, view fetching, error handling.
- Integration Adapter: environment flags, local routing, module patching/unpatching.
- CLI: argument parsing, end-to-end file generation, exit codes.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

import headshots.local as hl
from headshots.local.adapter import (
    get_local_generator_if_enabled,
    is_local_headshots_enabled,
    patch_headshot_generator,
    unpatch_headshot_generator,
)
from headshots.local.backends import (
    ComfyUIBackend,
    DiffusersBackend,
    HeadshotBackend,
    MockBackend,
    get_backend,
    register_backend,
)
from headshots.local.cli import main as cli_main, parse_args
from headshots.local.generator import PNG_MAGIC, generate_headshot


def create_dummy_png_bytes(width: int = 128, height: int = 128, color: tuple = (255, 0, 0)) -> bytes:
    """Helper to generate in-memory PNG bytes."""
    im = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class TestMockBackend(unittest.TestCase):
    """Test MockBackend generation, reproducibility, and hooks."""

    def setUp(self):
        self.backend = MockBackend()
        self.prompt = "Photorealistic headshot of a Rams quarterback with #0B2265 jersey"

    def test_mock_backend_valid_png(self):
        png_bytes = self.backend.generate(self.prompt, seed=42, size=512)
        self.assertIsInstance(png_bytes, bytes)
        self.assertTrue(png_bytes.startswith(PNG_MAGIC))

        im = Image.open(io.BytesIO(png_bytes))
        self.assertEqual(im.format, "PNG")
        self.assertEqual(im.size, (512, 512))

    def test_mock_backend_seed_determinism(self):
        img1 = self.backend.generate(self.prompt, seed=1234, size=256)
        img2 = self.backend.generate(self.prompt, seed=1234, size=256)
        img3 = self.backend.generate(self.prompt, seed=9999, size=256)

        # Same seed and prompt -> exact identical bytes
        self.assertEqual(img1, img2)
        # Different seed -> different bytes
        self.assertNotEqual(img1, img3)

    def test_mock_backend_custom_sizes_and_aspect(self):
        # 1024 default size
        b1024 = self.backend.generate(self.prompt, seed=1, size=1024)
        im1024 = Image.open(io.BytesIO(b1024))
        self.assertEqual(im1024.size, (1024, 1024))

        # 4:3 aspect ratio config
        b43 = self.backend.generate(self.prompt, seed=1, size=1024, config={"aspect_ratio": "4:3"})
        im43 = Image.open(io.BytesIO(b43))
        self.assertEqual(im43.size, (1024, 768))

    def test_mock_backend_with_ref_image(self):
        ref_bytes = create_dummy_png_bytes(64, 64, (0, 255, 0))
        out_bytes = self.backend.generate(self.prompt, ref_image=ref_bytes, seed=42, size=512)
        im = Image.open(io.BytesIO(out_bytes))
        self.assertEqual(im.size, (512, 512))
        self.assertTrue(out_bytes.startswith(PNG_MAGIC))

    def test_mock_backend_with_controlnet_config(self):
        out_bytes = self.backend.generate(
            self.prompt,
            seed=42,
            size=256,
            config={"controlnet": True, "controlnet_model": "diffusers/controlnet-depth-sdxl-1.0"},
        )
        self.assertTrue(out_bytes.startswith(PNG_MAGIC))


class TestGeneratorInterface(unittest.TestCase):
    """Test public generate_headshot interface and registry."""

    def test_generate_headshot_default_mock(self):
        png_bytes = generate_headshot("Standard test prompt", seed=77, size=256)
        self.assertTrue(png_bytes.startswith(PNG_MAGIC))
        im = Image.open(io.BytesIO(png_bytes))
        self.assertEqual(im.size, (256, 256))

    def test_generate_headshot_explicit_backend(self):
        png_bytes = generate_headshot("Test prompt", backend="mock", seed=77, size=256)
        self.assertTrue(png_bytes.startswith(PNG_MAGIC))

    def test_generate_headshot_backend_instance(self):
        backend = MockBackend()
        png_bytes = generate_headshot("Test prompt", backend=backend, seed=77, size=256)
        self.assertTrue(png_bytes.startswith(PNG_MAGIC))

    def test_generate_headshot_empty_prompt_raises(self):
        with self.assertRaises(ValueError):
            generate_headshot("")
        with self.assertRaises(ValueError):
            generate_headshot("   ")

    def test_generate_headshot_unknown_backend_raises(self):
        with self.assertRaises(ValueError) as ctx:
            generate_headshot("Prompt", backend="unsupported_backend")
        self.assertIn("Unknown headshot backend", str(ctx.exception))

    def test_get_backend_environment_variable(self):
        with mock.patch.dict(os.environ, {"LOCAL_HEADSHOT_BACKEND": "mock"}):
            b = get_backend()
            self.assertIsInstance(b, MockBackend)

    def test_custom_backend_registration(self):
        class CustomBackend(HeadshotBackend):
            def generate(self, prompt, ref_image=None, seed=None, size=1024, config=None):
                return create_dummy_png_bytes(size, size, (10, 20, 30))

        register_backend("custom_test", CustomBackend)
        b = get_backend("custom_test")
        self.assertIsInstance(b, CustomBackend)

        out = generate_headshot("Custom prompt", backend="custom_test", size=128)
        self.assertTrue(out.startswith(PNG_MAGIC))


class TestDiffusersBackend(unittest.TestCase):
    """Test DiffusersBackend device detection, memory optimization, and hooks."""

    def test_device_detection_cuda(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available.return_value = True
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            backend = DiffusersBackend()
            self.assertEqual(backend.device, "cuda")

    def test_device_detection_mps(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends.mps.is_available.return_value = True
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            backend = DiffusersBackend()
            self.assertEqual(backend.device, "mps")

    def test_device_detection_cpu(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends.mps.is_available.return_value = False
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            backend = DiffusersBackend()
            self.assertEqual(backend.device, "cpu")

    def test_diffusers_missing_dependency_raises(self):
        backend = DiffusersBackend()
        with mock.patch.dict(sys.modules, {"diffusers": None}):
            with self.assertRaises((ImportError, TypeError)):
                backend._init_pipeline()

    def test_diffusers_generation_with_mocked_pipeline(self):
        backend = DiffusersBackend(config={"device": "cpu"})

        mock_pipe = mock.MagicMock()
        fake_pil = Image.new("RGB", (256, 256), (50, 100, 150))
        mock_output = mock.MagicMock()
        mock_output.images = [fake_pil]
        mock_pipe.return_value = mock_output

        backend._pipe = mock_pipe

        fake_torch = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            png_bytes = backend.generate(
                prompt="Diffusers test prompt",
                seed=42,
                size=256,
                config={"guidance_scale": 7.5, "steps": 20},
            )

        self.assertTrue(png_bytes.startswith(PNG_MAGIC))
        mock_pipe.assert_called_once()
        call_kwargs = mock_pipe.call_args[1]
        self.assertEqual(call_kwargs["prompt"], "Diffusers test prompt")
        self.assertEqual(call_kwargs["height"], 256)
        self.assertEqual(call_kwargs["width"], 256)
        self.assertEqual(call_kwargs["num_inference_steps"], 20)
        self.assertEqual(call_kwargs["guidance_scale"], 7.5)

    def test_diffusers_ip_adapter_hook(self):
        backend = DiffusersBackend(config={"device": "cpu"})
        mock_pipe = mock.MagicMock()
        fake_pil = Image.new("RGB", (256, 256), (0, 0, 0))
        mock_pipe.return_value = mock.MagicMock(images=[fake_pil])
        backend._pipe = mock_pipe

        ref_bytes = create_dummy_png_bytes(64, 64)
        fake_torch = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            backend.generate(
                prompt="IP-Adapter test",
                ref_image=ref_bytes,
                seed=10,
                size=256,
            )

        call_kwargs = mock_pipe.call_args[1]
        self.assertIn("ip_adapter_image", call_kwargs)
        self.assertIsInstance(call_kwargs["ip_adapter_image"], Image.Image)

    def test_diffusers_controlnet_hook(self):
        backend = DiffusersBackend(config={"device": "cpu"})
        mock_pipe = mock.MagicMock()
        fake_pil = Image.new("RGB", (256, 256), (0, 0, 0))
        mock_pipe.return_value = mock.MagicMock(images=[fake_pil])
        backend._pipe = mock_pipe

        control_bytes = create_dummy_png_bytes(256, 256)
        fake_torch = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            backend.generate(
                prompt="ControlNet test",
                seed=10,
                size=256,
                config={
                    "controlnet": True,
                    "controlnet_image": control_bytes,
                    "controlnet_conditioning_scale": 0.85,
                },
            )

        call_kwargs = mock_pipe.call_args[1]
        self.assertIn("image", call_kwargs)
        self.assertEqual(call_kwargs["controlnet_conditioning_scale"], 0.85)


class TestComfyUIBackend(unittest.TestCase):
    """Test ComfyUI client backend, workflow composition, and network error handling."""

    def test_comfyui_host_resolution(self):
        with mock.patch.dict(os.environ, {"COMFYUI_HOST": "http://192.168.1.100:8188"}):
            backend = ComfyUIBackend()
            self.assertEqual(backend.host, "http://192.168.1.100:8188")

    def test_comfyui_workflow_construction(self):
        backend = ComfyUIBackend()
        wf = backend._build_workflow(
            prompt="A quarterback portrait",
            seed=42,
            size=1024,
            ref_image_name="test_ref.png",
            config={"controlnet": True},
        )

        # Check KSampler node
        self.assertIn("3", wf)
        self.assertEqual(wf["3"]["class_type"], "KSampler")
        self.assertEqual(wf["3"]["inputs"]["seed"], 42)

        # Check CLIPTextEncode node
        self.assertEqual(wf["6"]["inputs"]["text"], "A quarterback portrait")

        # Check EmptyLatentImage node
        self.assertEqual(wf["5"]["inputs"]["width"], 1024)
        self.assertEqual(wf["5"]["inputs"]["height"], 1024)

        # Check IP-Adapter nodes
        self.assertIn("10", wf)
        self.assertEqual(wf["10"]["inputs"]["image"], "test_ref.png")
        self.assertIn("12", wf)
        self.assertEqual(wf["12"]["class_type"], "IPAdapterApply")

        # Check ControlNet nodes
        self.assertIn("13", wf)
        self.assertEqual(wf["13"]["class_type"], "ControlNetLoader")
        self.assertIn("14", wf)
        self.assertEqual(wf["14"]["class_type"], "ControlNetApply")

    @mock.patch("requests.get")
    @mock.patch("requests.post")
    def test_comfyui_generate_success(self, mock_post, mock_get):
        backend = ComfyUIBackend(config={"host": "http://testserver:8188", "timeout": 5.0})

        # Mock /upload/image
        mock_upload_resp = mock.MagicMock()
        mock_upload_resp.json.return_value = {"name": "uploaded_ref.png"}
        mock_upload_resp.raise_for_status.return_value = None

        # Mock /prompt
        mock_prompt_resp = mock.MagicMock()
        mock_prompt_resp.json.return_value = {"prompt_id": "job-123"}
        mock_prompt_resp.raise_for_status.return_value = None

        mock_post.side_effect = [mock_upload_resp, mock_prompt_resp]

        # Mock /history/job-123
        mock_hist_resp = mock.MagicMock()
        mock_hist_resp.json.return_value = {
            "job-123": {
                "outputs": {
                    "9": {"images": [{"filename": "headshot_001.png", "subfolder": "", "type": "output"}]}
                }
            }
        }
        mock_hist_resp.raise_for_status.return_value = None

        # Mock /view
        fake_png = create_dummy_png_bytes(512, 512)
        mock_view_resp = mock.MagicMock()
        mock_view_resp.content = fake_png
        mock_view_resp.raise_for_status.return_value = None

        mock_get.side_effect = [mock_hist_resp, mock_view_resp]

        res = backend.generate(
            prompt="Chiefs tight end",
            ref_image=b"fake_ref_bytes",
            seed=42,
            size=512,
        )

        self.assertEqual(res, fake_png)
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_get.call_count, 2)

    @mock.patch("requests.post")
    def test_comfyui_connection_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        backend = ComfyUIBackend(config={"host": "http://127.0.0.1:8188"})
        with self.assertRaises(ConnectionError) as ctx:
            backend.generate("Test prompt")
        self.assertIn("Cannot connect to ComfyUI", str(ctx.exception))

    @mock.patch("requests.get")
    @mock.patch("requests.post")
    def test_comfyui_timeout_error(self, mock_post, mock_get):
        backend = ComfyUIBackend(config={"timeout": 0.2, "poll_interval": 0.05})

        mock_prompt_resp = mock.MagicMock()
        mock_prompt_resp.json.return_value = {"prompt_id": "job-timeout"}
        mock_post.return_value = mock_prompt_resp

        # Empty history
        mock_hist_resp = mock.MagicMock()
        mock_hist_resp.json.return_value = {}
        mock_get.return_value = mock_hist_resp

        with self.assertRaises(TimeoutError) as ctx:
            backend.generate("Test prompt")
        self.assertIn("timed out", str(ctx.exception))


class TestAdapter(unittest.TestCase):
    """Test adapter routing and patching mechanisms."""

    def tearDown(self):
        unpatch_headshot_generator()

    def test_is_local_headshots_enabled(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_local_headshots_enabled())

        with mock.patch.dict(os.environ, {"USE_LOCAL_HEADSHOTS": "1"}):
            self.assertTrue(is_local_headshots_enabled())

        with mock.patch.dict(os.environ, {"LOCAL_HEADSHOTS": "true"}):
            self.assertTrue(is_local_headshots_enabled())

    def test_get_local_generator_if_enabled(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(get_local_generator_if_enabled())

        with mock.patch.dict(os.environ, {"USE_LOCAL_HEADSHOTS": "1", "LOCAL_HEADSHOT_BACKEND": "mock"}):
            gen = get_local_generator_if_enabled()
            self.assertIsNotNone(gen)
            png = gen(prompt="Local adapter test", seed=10)
            self.assertTrue(png.startswith(PNG_MAGIC))

    def test_patch_and_unpatch_make_headshot(self):
        import headshots.make_headshot as m_make

        original_fn = m_make.make_headshot

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_file = os.path.join(tmpdir, "player_test.png")
                # Force patch with mock backend
                patched = patch_headshot_generator(force=True, backend="mock")
                self.assertTrue(patched)
                self.assertNotEqual(m_make.make_headshot, original_fn)

                res_path = m_make.make_headshot(
                    name="Marcus Stone",
                    team="Rams",
                    pos="QB",
                    height="6'3\"",
                    weight=220,
                    age=24,
                    out=out_file,
                )
                self.assertEqual(res_path, out_file)
                self.assertTrue(os.path.exists(out_file))
                self.assertGreater(os.path.getsize(out_file), 1000)

                unpatch_headshot_generator()
                self.assertEqual(m_make.make_headshot, original_fn)
        finally:
            unpatch_headshot_generator()

    def test_patch_and_unpatch_rookie_headshots(self):
        import headshots.rookie_headshots as r_make

        original_fn = r_make.generate_player_headshot
        fake_img = Image.new("RGBA", (700, 508), (255, 0, 0, 255))

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                static_dir = os.path.join(tmpdir, "static", "players")
                backup_dir = os.path.join(tmpdir, "backup")

                patched = patch_headshot_generator(force=True, backend="mock")
                self.assertTrue(patched)
                self.assertNotEqual(r_make.generate_player_headshot, original_fn)

                mock_rembg_session = mock.MagicMock()
                with mock.patch("headshots.rookie_headshots.get_thread_rembg_session", return_value=mock_rembg_session):
                    with mock.patch("rembg.remove", return_value=fake_img):
                        player = {
                            "id": 201,
                            "full_name": "Marcus Stone",
                            "team": "Rams",
                            "position": "QB",
                            "height": "6'3\"",
                            "weight": 220,
                            "age": 22,
                        }
                        out_path = r_make.generate_player_headshot(player, static_dir, backup_dir)
                        self.assertTrue(os.path.exists(out_path))
                        self.assertEqual(out_path, os.path.join(static_dir, "201.webp"))

                unpatch_headshot_generator()
                self.assertEqual(r_make.generate_player_headshot, original_fn)
        finally:
            unpatch_headshot_generator()


class TestCLI(unittest.TestCase):
    """Test command-line interface execution."""

    def test_cli_argument_parsing(self):
        args = parse_args(["--prompt", "NFL quarterback", "--seed", "42", "--size", "512", "--backend", "mock"])
        self.assertEqual(args.prompt, "NFL quarterback")
        self.assertEqual(args.seed, 42)
        self.assertEqual(args.size, 512)
        self.assertEqual(args.backend, "mock")

    def test_cli_main_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "cli_test.png")
            ret = cli_main([
                "--prompt", "Photorealistic wide receiver",
                "--out", out_path,
                "--backend", "mock",
                "--seed", "99",
                "--size", "256",
            ])
            self.assertEqual(ret, 0)
            self.assertTrue(os.path.exists(out_path))
            im = Image.open(out_path)
            self.assertEqual(im.size, (256, 256))

    def test_cli_main_with_ref_image(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ref_path = os.path.join(tmpdir, "ref.png")
            with open(ref_path, "wb") as f:
                f.write(create_dummy_png_bytes(64, 64))

            out_path = os.path.join(tmpdir, "out_with_ref.png")
            ret = cli_main([
                "--prompt", "With face ref",
                "--ref-image", ref_path,
                "--out", out_path,
                "--backend", "mock",
                "--size", "256",
            ])
            self.assertEqual(ret, 0)
            self.assertTrue(os.path.exists(out_path))

    def test_cli_missing_ref_image_error(self):
        ret = cli_main([
            "--prompt", "Missing ref test",
            "--ref-image", "/nonexistent/ref_image_12345.png",
            "--backend", "mock",
        ])
        self.assertEqual(ret, 1)

    def test_cli_subprocess_invocation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "subproc_headshot.png")
            cmd = [
                sys.executable,
                "-m",
                "headshots.local.cli",
                "--prompt",
                "Subprocess test headshot",
                "--out",
                out_path,
                "--backend",
                "mock",
                "--seed",
                "42",
                "--size",
                "256",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")
            self.assertTrue(os.path.exists(out_path))
            im = Image.open(out_path)
            self.assertEqual(im.size, (256, 256))



class TestJerseyPlateCompositing(unittest.TestCase):
    """Test two-stage jersey plate extraction and player compositing."""

    def test_extract_clean_jersey_plate(self):
        from headshots.local.jersey_plate import extract_clean_jersey_plate

        # Create dummy jersey photo (896x1200) with blue jersey at bottom and skin on top
        im = Image.new("RGBA", (896, 1200), (255, 255, 255, 255))
        arr = np.array(im)
        # Top half: skin tone (r > b)
        arr[:700, :, :3] = [200, 150, 120]
        # Bottom half: blue jersey (b > r + 30)
        arr[700:, :, :3] = [0, 100, 200]

        test_img = Image.fromarray(arr)
        plate = extract_clean_jersey_plate(test_img, collar_y_min=650, collar_y_max=750)
        plate_arr = np.array(plate)

        # Above y=700 should be alpha 0
        self.assertEqual(plate_arr[600, 448, 3], 0)
        # Below y=750 on jersey should be opaque
        self.assertGreater(plate_arr[800, 448, 3], 0)

    def test_composite_headshot_to_jersey(self):
        from headshots.local.jersey_plate import composite_headshot_to_jersey

        # Create dummy player cutout (896x1152)
        player = Image.new("RGBA", (896, 1152), (200, 150, 120, 255))
        # Create dummy jersey plate (896x1200)
        plate = Image.new("RGBA", (896, 1200), (0, 0, 0, 0))
        plate_arr = np.array(plate)
        # Jersey opaque below y=700
        plate_arr[700:, :, :] = [0, 100, 200, 255]
        plate_img = Image.fromarray(plate_arr)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "test_composite.png")
            res = composite_headshot_to_jersey(
                player_cutout=player,
                jersey_plate=plate_img,
                scale=0.78,
                target_chin_y=590,
                target_center_x=440,
                out_format="broadcast",
                output_path=out_file,
            )
            self.assertTrue(os.path.exists(out_file))
            self.assertEqual(res.size, (800, 1000))


if __name__ == "__main__":
    unittest.main()

