FROM python:3
WORKDIR /app
COPY app.py .
COPY pyproject.toml uv.lock ./
RUN pip install uv
RUN uv pip install --system --all
CMD ["python", "app.py"]