# DGPP image pinned to a known upstream revision for NVIDIA DGX Spark / GB10.
FROM nvidia/cuda:13.0.2-devel-ubuntu24.04 AS build

ARG DEBIAN_FRONTEND=noninteractive
ARG BUILD_JOBS=4
ARG DGPP_REPOSITORY=https://github.com/HawkBearPig/dgpp.git
ARG DGPP_REF

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        libibverbs-dev \
        pkg-config \
        python3 \
    && rm -rf /var/lib/apt/lists/*

RUN git init /opt/dgpp \
    && git -C /opt/dgpp remote add origin "${DGPP_REPOSITORY}" \
    && git -C /opt/dgpp fetch --depth 1 origin "${DGPP_REF}" \
    && git -C /opt/dgpp checkout --detach FETCH_HEAD

WORKDIR /opt/dgpp

RUN CUDACXX=/usr/local/cuda/bin/nvcc cmake --preset release \
    && cmake --build --preset release --target dgpp_serve_app -j "${BUILD_JOBS}"

FROM nvidia/cuda:13.0.2-runtime-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        ibverbs-providers \
        libibverbs1 \
        libnl-3-200 \
        libnl-route-3-200 \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/dgpp/build-release/dgpp-serve /usr/local/bin/dgpp-serve

CMD ["bash"]
