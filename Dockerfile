FROM python:3
WORKDIR /app
COPY app.py .
RUN pip install flask
RUN pip install requests
RUN pip install python-dotenv
CMD ["python", "app.py"]