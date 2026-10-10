FROM python:3.12-slim-bookworm
ARG PICO_DRIVER_VERSION=2.2.265-2r7906
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg libusb-1.0-0 libstdc++6 \
    && curl -fsSL -A 'Mozilla/5.0' https://labs.picotech.com/Release.gpg.key | gpg --dearmor -o /usr/share/keyrings/picotech.gpg \
    && printf '%s\n' 'deb [signed-by=/usr/share/keyrings/picotech.gpg] https://labs.picotech.com/rc/picoscope7/debian/ picoscope main' > /etc/apt/sources.list.d/picotech.list \
    && apt-get update && apt-get install -y --no-install-recommends libps4000a=${PICO_DRIVER_VERSION} \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt pyvisa pyvisa-py pyusb
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LD_LIBRARY_PATH=/opt/picoscope/lib HOME=/tmp
USER 1000:1000
EXPOSE 4824
STOPSIGNAL SIGTERM
CMD ["python", "-B", "-m", "pico4824a", "web", "--host", "0.0.0.0", "--port", "4824"]
