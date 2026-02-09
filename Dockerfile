FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y git

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Expose port for Render
EXPOSE 8080

# CRITICAL: Ensure this line is formatted exactly like this
CMD ["python", "bot.py"]
