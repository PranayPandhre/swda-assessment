FROM python:3.12-slim@sha256:REPLACE_WITH_PINNED_DIGEST
RUN useradd -r -u 10001 app
WORKDIR /app
COPY pyproject.toml ./
COPY part1_system_design ./part1_system_design
COPY part2_case_study ./part2_case_study
RUN pip install --no-cache-dir .
USER 10001
EXPOSE 8808
# Audit HMAC key and client tokens come from the platform secret store, never the image.
CMD ["swda-gateway", "serve-http"]
