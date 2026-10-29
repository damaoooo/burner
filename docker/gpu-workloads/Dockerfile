FROM pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/root/.cache/huggingface \
    TORCH_HOME=/root/.cache/torch

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        git \
        libgl1 \
        libglib2.0-0 \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --upgrade pip \
    && python3 -m pip install --no-cache-dir \
        torchvision==0.20.1 \
        transformers \
        accelerate \
        diffusers \
        sentence-transformers \
        safetensors \
        pillow \
        numpy \
    && (python3 -m pip install --no-cache-dir faiss-gpu-cu12 || python3 -m pip install --no-cache-dir faiss-cpu)

WORKDIR /opt/burner
COPY gpu_workloads ./gpu_workloads

ENV PYTHONPATH=/opt/burner

CMD ["python3", "-m", "gpu_workloads.runner", "--help"]
