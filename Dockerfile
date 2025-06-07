FROM python:3.11-slim

# Install system dependencies for Chrome/Chromium
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Poetry
RUN pip install poetry

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install Python dependencies (including playwright)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root

# Set the browsers path environment variable BEFORE installing browsers
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install Playwright browsers - this is the critical part
RUN python -m playwright install chromium --with-deps

# Verify the installation
RUN ls -la /ms-playwright/chromium-*/chrome-linux/chrome || echo "Chrome binary not found!"

# Copy application code
COPY jobber_fsm ./jobber_fsm

# Set environment variables
ENV OPENAI_API_KEY=""
ENV LANGCHAIN_API_KEY=""

CMD ["python", "-u", "-m", "jobber_fsm.cloud_job_runner"]