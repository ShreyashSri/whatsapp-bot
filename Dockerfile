FROM python:3.12-slim-bookworm

# System dependencies for Playwright's Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    dumb-init \
    fonts-liberation \
    fonts-noto-color-emoji \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Playwright browsers in a shared location readable by the runtime
# user instead of root's private cache.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Keep bot logs visible to the container runtime.
ENV PYTHONUNBUFFERED=1

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

# Create directories for runtime data
RUN mkdir -p /app/data \
    && useradd --create-home botuser \
    && chown -R botuser:botuser /app
USER botuser

EXPOSE 8081 8082

ENTRYPOINT ["dumb-init", "--"]
CMD ["python", "bot.py"]
