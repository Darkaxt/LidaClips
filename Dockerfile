FROM python:3.12-alpine

ARG RELEASE_VERSION
ENV RELEASE_VERSION=${RELEASE_VERSION}

RUN apk update && apk add --no-cache ffmpeg nodejs su-exec

COPY . /lidaclips
WORKDIR /lidaclips

ENV PYTHONPATH=/lidaclips/src
RUN pip install --no-cache-dir -r requirements.txt

RUN chmod +x lidaclips-init.sh

EXPOSE 5000

ENTRYPOINT ["./lidaclips-init.sh"]

