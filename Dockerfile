FROM python:3.11.8-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем только необходимые файлы
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код проекта
COPY . .

# Создаём директорию для persistent данных (БД)
VOLUME /app/data

# Запускаем бота
CMD ["python", "bot.py"]