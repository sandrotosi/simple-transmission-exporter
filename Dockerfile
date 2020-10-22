FROM python:3.9
COPY ./requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt
COPY ./simple_transmission_exporter.py /app/simple_transmission_exporter.py

EXPOSE 29091
CMD /app/simple_transmission_exporter.py