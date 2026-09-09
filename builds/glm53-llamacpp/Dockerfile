# GLM-5.3-Flash llama.cpp image for a single DGX Spark / GB10.
#
# This intentionally uses CUDA 13.0.2 and builds for SM 12.1. It also uses
# eauchs' glm5next branch (llama.cpp PR #27752), which has the native MTP/NextN
# draft graph used by the Q2 recipe.

FROM nvidia/cuda:13.0.2-devel-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG BUILD_JOBS=20
ARG LLAMA_CPP_REPO=https://github.com/eauchs/llama.cpp.git
ARG LLAMA_CPP_REF=glm5next/add-glm-5.3-flash

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        libssl-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${LLAMA_CPP_REF}" \
        "${LLAMA_CPP_REPO}" /opt/llama.cpp

WORKDIR /opt/llama.cpp

# Explicit CUDACXX matters on DGX OS/non-login build environments.
# 121 is the GB10 / DGX Spark CUDA architecture.
RUN CUDACXX=/usr/local/cuda/bin/nvcc \
    cmake -S . -B build \
        -DGGML_CUDA=ON \
        -DBUILD_SHARED_LIBS=OFF \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CUDA_ARCHITECTURES=121 \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
        -DLLAMA_BUILD_SERVER=ON \
    && cmake --build build --target llama-server -j "${BUILD_JOBS}" \
    && install -m 0755 build/bin/llama-server /usr/local/bin/llama-server

# run-recipe/launch-cluster supplies the actual command.
CMD ["bash"]
