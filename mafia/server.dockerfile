FROM python:3.8-slim

WORKDIR /mafia/
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY server.py server.py
COPY config.ini config.ini
COPY protos /mafia/protos/

RUN python3 -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. --pyi_out=. ./protos/request.proto

ENTRYPOINT ["python3", "-m", "server"]
