FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY contest_config/all_things_agentic.requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.txt

COPY kira_studio /app/kira_studio
COPY web/all-things-agentic.html /app/web/all-things-agentic.html
COPY all_things_cloud_app.py /app/all_things_cloud_app.py

RUN addgroup --system app && adduser --system --ingroup app app \
    && chown --recursive app:app /app
USER app

EXPOSE 8080
CMD ["python", "all_things_cloud_app.py"]
