FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin yukino
COPY pyproject.toml /app/
COPY yukino_core /app/yukino_core
COPY resources /app/resources
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
CMD ["python", "-m", "yukino_core", "--host", "0.0.0.0", "--data", "/data", "--resources", "/data/resources"]
