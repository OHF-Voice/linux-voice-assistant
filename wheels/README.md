Place the two build artifacts here (both are large binaries, gitignored, so
they must be downloaded/placed manually):

1. sherpa_onnx-<version>-cp313-cp313-manylinux_2_27_aarch64.whl —
   the sherpa-onnx Python wheel built with RK3588 (RKNPU) support. Built by
   the k2-fsa CI and published at:

       https://huggingface.co/csukuangfj2/sherpa-onnx-wheels/tree/main/rknn/<version>

   direct link for the current version:

       https://huggingface.co/csukuangfj2/sherpa-onnx-wheels/resolve/main/rknn/1.13.8/sherpa_onnx-1.13.8-cp313-cp313-manylinux_2_27_aarch64.whl

   e.g. rknn/1.13.8/sherpa_onnx-1.13.8-cp313-cp313-manylinux_2_27_aarch64.whl.
   A locally built wheel (sherpa-onnx configured with -DSHERPA_ONNX_ENABLE_RKNN=ON
   against librknnrt) works too and may differ from the HF build by checksum.
   These wheels are built WITHOUT librknnrt.so inside (the CI excludes it) —
   which is exactly why the Dockerfile stages the library from the SDK below.

2. RKNPU2_SDK-<version>.tgz — Rockchip RKNPU2 SDK; the Docker build uses
   runtime/Linux/librknn_api/aarch64/librknnrt.so from it. Official repo:

       https://github.com/airockchip/rknn-toolkit2

   (rknpu2/runtime/, pick the tag matching your librknnrt, e.g. v2.5.0).
   The current RKNPU2_SDK-2.4.2a7.tgz is a Rockchip alpha bundle
   (librknnrt 2.4.2a2) that is not published among the public GitHub tags.

Keep the exact cp313 wheel tag: the image is python:3.13-slim. The
librknnrt.so version must be compatible with the wheel's RKNN API (see
versions.txt inside the SDK archive) — if you update one, re-check the pair.
