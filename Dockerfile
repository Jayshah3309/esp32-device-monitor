FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY monitor.py simulator.py dashboard.html ./
RUN useradd --create-home monitor && mkdir /app/data && chown monitor:monitor /app/data
USER monitor
CMD ["python", "monitor.py", "--host", "0.0.0.0"]
