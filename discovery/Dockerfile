FROM python:3.12-alpine
RUN apk add --no-cache ffmpeg && addgroup -g 1000 discovery && adduser -D -u 1000 -G discovery discovery
WORKDIR /app
COPY common.py server.py ./
RUN chmod 644 /app/common.py /app/server.py
USER 1000:1000
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python3", "/app/server.py"]
