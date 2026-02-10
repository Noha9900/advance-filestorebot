FROM python:3.10-slim

# Install system dependencies needed for building Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Port for Render
EXPOSE 8080

CMD ["python", "bot.py"]
