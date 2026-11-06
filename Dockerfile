FROM python:3.12-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7

ARG RELEASE_VERSION
ARG VCS_REF
ENV RELEASE_VERSION=${RELEASE_VERSION}
LABEL org.opencontainers.image.source="https://github.com/Darkaxt/LidaClips" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.version="${RELEASE_VERSION}"

RUN apk update && apk add --no-cache ffmpeg nodejs su-exec

COPY . /lidaclips
WORKDIR /lidaclips

ENV PYTHONPATH=/lidaclips/src
RUN pip install --no-cache-dir -r requirements.txt

RUN chmod +x lidaclips-init.sh

EXPOSE 5000

ENTRYPOINT ["./lidaclips-init.sh"]

