FROM python:3.10-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
# Port for Render (though TG bots use webhooks or polling)
EXPOSE 8080
CMD ["python", "bot.py"]
