# PrismML llama.cpp for Bonsai 2 on one DGX Spark / GB10.
#
# Bonsai 2 requires PrismML's Hadamard activation transform and PTQ1_0 CUDA
# kernels; stock llama.cpp cannot load this packing. Pin the exact fork release
# used by the Bonsai demo rather than following a moving branch.

FROM nvidia/cuda:13.0.2-devel-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG BUILD_JOBS=20
ARG LLAMA_CPP_REPO=https://github.com/PrismML-Eng/llama.cpp.git
ARG LLAMA_CPP_REF=9a9394a895b96003ca842a6041cb28ac49a108f7

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        libcurl4-openssl-dev \
        libssl-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --filter=blob:none --no-checkout \
        "${LLAMA_CPP_REPO}" /opt/llama.cpp \
    && git -C /opt/llama.cpp fetch --depth 1 origin "${LLAMA_CPP_REF}" \
    && git -C /opt/llama.cpp checkout --detach FETCH_HEAD

WORKDIR /opt/llama.cpp

# 121 is the GB10 / DGX Spark CUDA architecture. CURL support enables the
# recipe's selective Hugging Face download, including automatic mmproj lookup.
RUN CUDACXX=/usr/local/cuda/bin/nvcc \
    cmake -S . -B build \
        -DGGML_CUDA=ON \
        -DBUILD_SHARED_LIBS=OFF \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CUDA_ARCHITECTURES=121 \
        -DLLAMA_CURL=ON \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
        -DLLAMA_BUILD_SERVER=ON \
    && cmake --build build --target llama-server -j "${BUILD_JOBS}" \
    && install -m 0755 build/bin/llama-server /usr/local/bin/llama-server

# run-recipe/launch-cluster supplies the actual command.
CMD ["bash"]

