# Docker GPU workloads

GPU workload mode runs real single-GPU data-center style tasks in Docker. It is
separate from `./burner --gpu`, which still uses patched `gpu_burn` for
curve-controlled synthetic burn.

## Requirements

The host running the workload needs:

- NVIDIA driver and a visible GPU;
- Docker;
- NVIDIA Container Toolkit configured so `docker run --gpus ...` works;
- network access for the first model downloads.

The default Docker image is:

```text
burner-gpu-workloads:latest
```

The image uses a PyTorch CUDA base and installs Transformers, Diffusers,
Sentence Transformers, FFmpeg, and FAISS when available. Model cache is mounted
as a Docker volume:

```text
burner_gpu_cache:/root/.cache
```

This keeps repeated runs from downloading the same public models again.

## Workloads

The default scenario is:

```text
UI/gpu_scenarios/single-gpu-default.json
```

It runs tasks sequentially on one GPU:

| Workload | Purpose |
| --- | --- |
| `gemm` | cuBLAS/PyTorch matrix multiply baseline. |
| `memory-bandwidth` | Tensor copy, elementwise, and reduction bandwidth pressure. |
| `cv-train` | Torchvision model training loop on synthetic image batches. |
| `cv-infer` | Torchvision pretrained image model inference. |
| `llm-infer` | HuggingFace causal LM prefill/decode generation. |
| `embedding-infer` | Sentence Transformers embedding generation. |
| `diffusion-infer` | Diffusers text-to-image inference. |
| `video-transcode` | FFmpeg NVENC/NVDEC-style transcode workload. |
| `video-analytics` | Video-like batches through a CV inference model. |
| `faiss-search` | FAISS GPU vector search when GPU FAISS is installed. |

Durations are workload runtime targets. First runs may take longer because
public model weights are downloaded before the hot loop begins.

## Local CLI

Build the image on the local machine:

```bash
python -m gpu_workloads.cli build-image
```

Run the default scenario on GPU 0:

```bash
python -m gpu_workloads.cli run-local \
  --scenario UI/gpu_scenarios/single-gpu-default.json \
  --gpu 0
```

Run a single workload without writing a scenario JSON:

```bash
python -m gpu_workloads.cli run-task gemm \
  --duration 1m \
  --gpu 0 \
  --precision fp16 \
  --matrix-size 4096
```

The `run-task` command accepts these common arguments:

| Argument | Meaning |
| --- | --- |
| `workload` | One of the workload names in the table above. |
| `--duration` | Runtime target in seconds, or with `s`, `m`, `h` suffixes such as `60s`, `1m`, `0.5h`. |
| `--gpu` | Host GPU index to pass to Docker. Defaults to `0`. |
| `--image` | Docker image name. Defaults to `burner-gpu-workloads:latest`. |
| `--container-name` | Local Docker container name. |
| `--model` | Model name for model-backed workloads. |
| `--batch-size` | Batch size or FAISS query count. Defaults to `1`. |
| `--input-shape` | Tensor/image shape such as `3,224,224` or `3x224x224`. |
| `--precision` | `fp16`, `fp32`, or `bf16`. Defaults to `fp16`. |
| `--param KEY=VALUE` | Extra workload parameter. May be repeated. |

Common workload-specific flags are:

| Flag | Used by |
| --- | --- |
| `--matrix-size` | `gemm` |
| `--elements` | `memory-bandwidth` |
| `--classes` | `cv-train` |
| `--prompt`, `--max-new-tokens` | `llm-infer` |
| `--prompt`, `--steps`, `--height`, `--width` | `diffusion-infer` |
| `--width`, `--height`, `--fps`, `--codec`, `--preset` | `video-transcode` |
| `--vectors`, `--dimension` | `faiss-search` |

Stop the default local container:

```bash
python -m gpu_workloads.cli stop-local
```

## WebUI

The WebUI GPU Workload panel supports one remote machine and one GPU at a time:

1. Connect a GPU machine and refresh hardware info.
2. Select the machine, scenario, GPU index, and image name.
3. Click **Setup Image** to SCP the Docker context, build the image remotely,
   and verify CUDA inside the container.
4. Click **Start GPU Scenario** to run the scenario in a single Docker
   container with `--gpus '"device=0"'`.
5. Click **Stop GPU Jobs** to `docker stop`, then `docker kill` if needed.
