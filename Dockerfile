FROM python:3.11.11-slim

WORKDIR /usr/src/app
ENV PYTHONUNBUFFERED 1

RUN apt update && apt install -y \
    ffmpeg \
    libblas3 \
    libopenblas0 \
    liblapack3 \
    libstdc++6 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Allows docker to cache installed dependencies between builds

COPY ./requirements.txt requirements.txt
RUN pip install -r requirements.txt

# Adds our application code to the image
COPY . .
