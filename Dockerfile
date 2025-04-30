FROM python:3
WORKDIR /app
COPY app.py .
RUN pip install flask
CMD ["python", "app.py"]