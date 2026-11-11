FROM python:3.12-alpine
RUN adduser -D -u 10001 sink
COPY app.py /app/app.py
USER sink
EXPOSE 8090
CMD ["python", "-u", "/app/app.py"]
