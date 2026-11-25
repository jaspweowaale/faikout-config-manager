FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY faikout_config/ faikout_config/
COPY webui/ webui/

ENV FAIKOUT_CM_DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8765

# Live writes stay off unless the operator deliberately sets this at
# `docker run`/compose time -- never flip it in the image itself.
ENV ENABLE_LIVE_WRITES=false

CMD ["python3", "webui/app.py"]
