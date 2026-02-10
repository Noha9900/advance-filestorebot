FROM python:3.10-slim

# Install system dependencies
# 1. build-essential & python3-dev: for compiling C extensions (needed by some python libs)
# 2. ca-certificates: Ensures SSL connections (MongoDB/Telegram) work correctly
# 3. ffmpeg: Highly recommended for any Telegram bot handling video/audio to avoid format errors
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    ca-certificates \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip to ensure latest wheel handling
RUN pip install --upgrade pip

# Install Python requirements
# Added --no-cache-dir to keep the image small
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Port for Render (Required by Render's health check)
EXPOSE 8080

# Start the bot
CMD ["python", "bot.py"]
