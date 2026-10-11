# Base image: small official Python image
FROM python:3.12-slim

# Show logs immediately; don't keep pip's download cache in the image
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# All following commands run inside /app in the container
WORKDIR /app

# Copy project metadata and source code into the image
COPY pyproject.toml ./
COPY src ./src

# Install the pipeline package and its dependencies (pandas, psycopg)
RUN pip install .

# Default command: run our CLI (extract / transform / validate / load)
ENTRYPOINT ["python", "-m", "pipeline.cli"]
# If no subcommand is given, just show the help text
CMD ["--help"]