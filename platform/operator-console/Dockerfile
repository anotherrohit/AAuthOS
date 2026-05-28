FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=9002
WORKDIR /app
COPY index.html app.js server.py ./
EXPOSE 9002
CMD ["python", "server.py"]
