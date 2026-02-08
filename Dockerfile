# Use a lightweight Python version
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the bot code
COPY . .

# Expose port 8080 for the fake web server (Required by Render)
EXPOSE 8080

# Command to run the bot
CMD ["python", "main.py"]
