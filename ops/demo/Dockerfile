FROM python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89 AS build

ARG SOURCE_SHA
WORKDIR /build
COPY backend/ ./backend/
COPY ops/demo/verify_ui_build.py ./verify_ui_build.py
RUN python verify_ui_build.py backend/src/autonomo_taxes/web_ui/dist "${SOURCE_SHA}" \
    && python -m pip wheel --no-cache-dir --wheel-dir /wheels ./backend

FROM python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89

ARG SOURCE_SHA
LABEL org.opencontainers.image.source="https://github.com/svonidze/spain-autonomo-taxes" \
      org.opencontainers.image.revision="${SOURCE_SHA}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/nonexistent
COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels spain-autonomo-taxes \
    && rm -rf /wheels
COPY reference/reta/2026.json /app/reference/reta/2026.json
WORKDIR /app
USER 10001:10001
EXPOSE 8765
CMD ["python", "-m", "autonomo_taxes.local_web", "--public-demo-root", "/data", "--host", "0.0.0.0", "--port", "8765"]
