FROM python:3.10-slim

WORKDIR /app

# Install dependencies for Pyrogram and encryption
RUN apt-get update && apt-get install -y gcc python3-dev

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# This line is crucial—it copies your bot.py into /app
COPY . .

# Ensure the filename here matches your script exactly
CMD ["python", "bot.py"]
