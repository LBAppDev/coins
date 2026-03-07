FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip uninstall -y discord discord.py || true
RUN pip install --no-cache-dir -U git+https://github.com/dolfies/discord.py-self.git
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import discord; print('discord module:', getattr(discord, '__file__', 'n/a')); print('discord version:', getattr(discord, '__version__', 'n/a')); print('has Intents:', hasattr(discord, 'Intents'))"

COPY . .

CMD ["python", "main.py"]
