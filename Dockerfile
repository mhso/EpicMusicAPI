FROM python:3.14-trixie
WORKDIR /epic_music

# Expose the provided port for FastAPI
EXPOSE $PORT

# Set environment variables
ENV UV_INSTALL_DIR=/bin

# Copy pyproject.toml
COPY pyproject.toml uv.lock ./

# Download UV and install requirements
RUN curl -LsSf https://astral.sh/uv/install.sh | bash && uv sync --no-dev

# Copy code and resources
COPY .env ./.env
COPY src ./src
COPY resources ./resources
COPY static ./static

# Run the server
WORKDIR /epic_music/src
CMD uv run uvicorn --host 0.0.0.0 --port ${PORT} main:app