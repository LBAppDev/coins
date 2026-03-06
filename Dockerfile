FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip uninstall -y discord.py || true
RUN pip install --no-cache-dir -U git+https://github.com/dolfies/discord.py-self.git
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
