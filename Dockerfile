FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY imv ./imv
RUN pip install --no-cache-dir .
ENV IMV_VAULT=/vault IMV_HTTP=1 IMV_PORT=8484
VOLUME ["/vault"]
EXPOSE 8484
CMD ["imv-server"]
